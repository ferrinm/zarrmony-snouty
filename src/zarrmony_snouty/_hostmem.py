"""Host-memory bound for the deshear and traditional transforms.

``adapter.py`` builds one dask task per timepoint, and each task transforms a
whole ``(C, Z, Y, X)`` volume. Dask's default scheduler is threaded with
``os.cpu_count()`` workers, so without a bound the host holds one whole
transformed volume per core. At the geometry ADR-0002 measures, ``traditional``
costs 4.90 GB per task, which is 157 GB across 32 threads.

This module caps that by handing out a process-wide budget measured in bytes.
Each transform reserves its own footprint before it runs and releases it
afterwards. A byte budget rather than a slot count matters because a
session-level convert opens many child readers whose geometries differ, so a
fixed slot count sized from one scene misbounds the others.

``raw`` reserves nothing. It runs no transform and holds only the input volume.

This is issue #15's approach 2. Approach 1, which chunks along Z inside the
transform so a task holds a slab rather than a whole volume, is the better fix
and composes on top of this one later.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import _deshear
from ._errors import SnoutyError

#: Fraction of the detected memory limit this process will spend on transforms.
#: The rest is headroom for the TIFF reads, the Zarr writes, the pyramid
#: mean-pool, and everything else sharing the host.
DEFAULT_BUDGET_FRACTION = 0.5

#: Absolute budget in bytes. Wins over the fraction when set.
BUDGET_BYTES_ENV = "ZARRMONY_SNOUTY_HOST_MEMORY_BYTES"

#: Fraction of the detected limit, as a float between 0 and 1.
BUDGET_FRACTION_ENV = "ZARRMONY_SNOUTY_HOST_MEMORY_FRACTION"

_CGROUP_V2_LIMIT = Path("/sys/fs/cgroup/memory.max")
_CGROUP_V1_LIMIT = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")

# A cgroup with no limit reports "max" (v2) or a sentinel near 2**63 (v1).
# Treat anything at or above this as unlimited rather than as a real ceiling.
_CGROUP_UNLIMITED_FLOOR = 2**62


class SnoutyHostMemoryError(SnoutyError, ValueError):
    """``ZARRMONY_SNOUTY_HOST_MEMORY_BYTES`` or
    ``ZARRMONY_SNOUTY_HOST_MEMORY_FRACTION`` is not a usable value."""


def transform_footprint_bytes(
    *,
    mode: str,
    size_z: int,
    size_y: int,
    size_x: int,
    n_channels: int,
    scan_step_size_px: float,
    voxel_aspect_ratio: float,
    itemsize: int,
) -> int:
    """Peak host bytes one dask task holds while it runs ``mode``.

    Derived from the real output shapes in :mod:`zarrmony_snouty._deshear`,
    never hardcoded, so a change to either shape formula moves the bound with
    it. Sizes are per the ADR-0002 table for acquisition A:

    - ``raw`` — no transform runs, so nothing is reserved.
    - ``desheared`` — the input volume plus the desheared output (2.47 GB).
    - ``traditional`` — the input, the desheared intermediate that
      :func:`_deshear.traditional_zyx` builds internally, and the rotated
      output (4.90 GB).
    """
    if mode == "raw":
        return 0

    raw_voxels = size_z * size_y * size_x
    desheared_voxels = _volume(_deshear.desheared_shape(size_z, size_y, size_x, scan_step_size_px))

    voxels = raw_voxels + desheared_voxels
    if mode == "traditional":
        voxels += _volume(
            _deshear.traditional_shape(
                size_z, size_y, size_x, scan_step_size_px, voxel_aspect_ratio
            )
        )
    return voxels * n_channels * itemsize


def _volume(shape: tuple[int, int, int]) -> int:
    z, y, x = shape
    return z * y * x


def cgroup_limit_bytes() -> int | None:
    """The memory ceiling this process's cgroup imposes, or ``None``.

    This is the number that matters inside a SLURM allocation. A 62 GB
    allocation on a 512 GB node reports 512 GB through ``os.sysconf``, so a
    budget taken from physical memory alone authorizes two and a half times
    what the allocation actually grants. Returns ``None`` off Linux, and when
    the cgroup declares no limit.
    """
    for path in (_CGROUP_V2_LIMIT, _CGROUP_V1_LIMIT):
        try:
            text = path.read_text().strip()
        except OSError:
            continue
        if text == "max":
            return None
        try:
            value = int(text)
        except ValueError:
            continue
        if value <= 0 or value >= _CGROUP_UNLIMITED_FLOOR:
            return None
        return value
    return None


def physical_memory_bytes() -> int | None:
    """Total physical RAM on this host, or ``None`` when unknowable."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        return None


def detected_limit_bytes() -> int | None:
    """The smaller of the cgroup ceiling and physical RAM."""
    candidates = [v for v in (cgroup_limit_bytes(), physical_memory_bytes()) if v]
    return min(candidates) if candidates else None


def budget_bytes() -> int | None:
    """The transform budget for this process, or ``None`` for no bound.

    Resolution order:

    1. ``ZARRMONY_SNOUTY_HOST_MEMORY_BYTES``, an absolute override. ``0``
       disables the bound entirely.
    2. The detected limit times ``ZARRMONY_SNOUTY_HOST_MEMORY_FRACTION``, or
       times :data:`DEFAULT_BUDGET_FRACTION` when that is unset.
    3. ``None`` when neither a cgroup ceiling nor physical RAM is readable.
    """
    raw = os.environ.get(BUDGET_BYTES_ENV)
    if raw is not None:
        try:
            value = int(raw)
        except ValueError as exc:
            raise SnoutyHostMemoryError(
                f"{BUDGET_BYTES_ENV}={raw!r} is not an integer number of bytes"
            ) from exc
        if value < 0:
            raise SnoutyHostMemoryError(f"{BUDGET_BYTES_ENV}={raw!r} must not be negative")
        return value or None

    limit = detected_limit_bytes()
    if limit is None:
        return None
    return int(limit * _budget_fraction())


def _budget_fraction() -> float:
    raw = os.environ.get(BUDGET_FRACTION_ENV)
    if raw is None:
        return DEFAULT_BUDGET_FRACTION
    try:
        fraction = float(raw)
    except ValueError as exc:
        raise SnoutyHostMemoryError(
            f"{BUDGET_FRACTION_ENV}={raw!r} is not a number between 0 and 1"
        ) from exc
    if not 0 < fraction <= 1:
        raise SnoutyHostMemoryError(
            f"{BUDGET_FRACTION_ENV}={raw!r} must be greater than 0 and at most 1"
        )
    return fraction


class _ByteBudget:
    """A process-wide budget that threads draw from and return.

    A reservation larger than the whole budget is clamped to the budget and
    waits for an idle moment, so an oversized task runs alone rather than
    deadlocking.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._in_use = 0
        self._capacity: int | None = None
        self._resolved = False
        self._peak_in_use = 0

    def capacity(self) -> int | None:
        """The budget, resolved once and cached for the process lifetime."""
        with self._condition:
            if not self._resolved:
                self._capacity = budget_bytes()
                self._resolved = True
            return self._capacity

    @contextmanager
    def reserve(self, nbytes: int) -> Iterator[None]:
        capacity = self.capacity()
        if capacity is None or nbytes <= 0:
            yield
            return
        want = min(nbytes, capacity)
        with self._condition:
            while self._in_use and self._in_use + want > capacity:
                self._condition.wait()
            self._in_use += want
            self._peak_in_use = max(self._peak_in_use, self._in_use)
        try:
            yield
        finally:
            with self._condition:
                self._in_use -= want
                self._condition.notify_all()

    def reset(self) -> None:
        """Drop the cached capacity so the next reservation re-reads the
        environment. For tests, and for a caller that changes the env vars
        after import."""
        with self._condition:
            self._in_use = 0
            self._capacity = None
            self._resolved = False
            self._peak_in_use = 0


_BUDGET = _ByteBudget()


def reserve(nbytes: int):
    """Hold ``nbytes`` of the process transform budget for the ``with`` block."""
    return _BUDGET.reserve(nbytes)


def capacity_bytes() -> int | None:
    """The resolved process budget, or ``None`` when transforms are unbounded."""
    return _BUDGET.capacity()


def reset() -> None:
    """Re-read the environment on the next reservation."""
    _BUDGET.reset()
