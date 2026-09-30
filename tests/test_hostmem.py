"""Host-memory bound for the transform modes (issue #15).

Expected footprints come from ADR-0002's measured table for acquisition A,
not from re-running the formula the implementation uses. Acquisition A is
``slices_per_volume: 411``, ``height_px: 600`` (592 after the timestamp-strip
crop), ``width_px: 1500``, ``scan_step_size_px: 2``,
``voxel_aspect_ratio: 2.856``, one channel, uint16.
"""

from __future__ import annotations

import threading
import time

import dask
import numpy as np
import pytest

from zarrmony_snouty import _deshear, _hostmem
from zarrmony_snouty.adapter import SnoutyReader

from .conftest import write_synthetic_snouty

# Acquisition A geometry, from ADR-0002 "Measurements".
ACQ_A = {
    "size_z": 411,
    "size_y": 592,
    "size_x": 1500,
    "n_channels": 1,
    "scan_step_size_px": 2.0,
    "voxel_aspect_ratio": 2.856,
    "itemsize": 2,
}

GB = 1000**3


def test_raw_mode_reserves_nothing():
    """``raw`` runs no transform, so it holds only the input volume and is
    not bounded. ADR-0002 measures it at 0.73 GB per task."""
    assert _hostmem.transform_footprint_bytes(mode="raw", **ACQ_A) == 0


def test_desheared_footprint_is_input_plus_output():
    """ADR-0002: input 0.73 GB + desheared output 1.74 GB = 2.47 GB."""
    got = _hostmem.transform_footprint_bytes(mode="desheared", **ACQ_A)
    assert got / GB == pytest.approx(2.47, abs=0.01)


def test_traditional_footprint_includes_the_desheared_intermediate():
    """ADR-0002: input 0.73 + intermediate 1.74 + output 2.43 = 4.90 GB.

    The intermediate is the part a naive input-plus-output estimate misses,
    and it is why ``traditional`` costs twice what ``desheared`` costs.
    """
    got = _hostmem.transform_footprint_bytes(mode="traditional", **ACQ_A)
    assert got / GB == pytest.approx(4.90, abs=0.01)


def test_footprint_scales_with_channel_count():
    """Two channels double every term, because the adapter transforms a
    whole ``(C, Z, Y, X)`` volume per dask task."""
    one = _hostmem.transform_footprint_bytes(mode="traditional", **ACQ_A)
    two = _hostmem.transform_footprint_bytes(mode="traditional", **{**ACQ_A, "n_channels": 2})
    assert two == 2 * one


# --- budget resolution -------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_budget(monkeypatch):
    """Every test resolves the budget from scratch, never from a cached
    capacity another test left behind."""
    monkeypatch.delenv(_hostmem.BUDGET_BYTES_ENV, raising=False)
    monkeypatch.delenv(_hostmem.BUDGET_FRACTION_ENV, raising=False)
    _hostmem.reset()
    yield
    _hostmem.reset()


def test_cgroup_limit_wins_over_physical_ram(monkeypatch):
    """The SLURM case. A 62 GB allocation on a 512 GB node must budget from
    62 GB. Physical RAM alone authorizes 2.5x what the allocation grants."""
    monkeypatch.setattr(_hostmem, "cgroup_limit_bytes", lambda: 62 * GB)
    monkeypatch.setattr(_hostmem, "physical_memory_bytes", lambda: 512 * GB)
    assert _hostmem.detected_limit_bytes() == 62 * GB


def test_physical_ram_wins_when_the_cgroup_declares_no_limit(monkeypatch):
    monkeypatch.setattr(_hostmem, "cgroup_limit_bytes", lambda: None)
    monkeypatch.setattr(_hostmem, "physical_memory_bytes", lambda: 64 * GB)
    assert _hostmem.detected_limit_bytes() == 64 * GB


def test_unlimited_cgroup_sentinels_are_not_treated_as_a_ceiling(monkeypatch, tmp_path):
    """cgroup v2 writes ``max`` and v1 writes a value near 2**63. Reading
    either as a real ceiling would produce a nonsense budget."""
    v2 = tmp_path / "memory.max"
    v2.write_text("max\n")
    monkeypatch.setattr(_hostmem, "_CGROUP_V2_LIMIT", v2)
    monkeypatch.setattr(_hostmem, "_CGROUP_V1_LIMIT", tmp_path / "absent")
    assert _hostmem.cgroup_limit_bytes() is None

    v2.write_text(str(2**63 - 1))
    assert _hostmem.cgroup_limit_bytes() is None


def test_cgroup_limit_is_read_from_the_v2_file(monkeypatch, tmp_path):
    v2 = tmp_path / "memory.max"
    v2.write_text(f"{62 * GB}\n")
    monkeypatch.setattr(_hostmem, "_CGROUP_V2_LIMIT", v2)
    assert _hostmem.cgroup_limit_bytes() == 62 * GB


def test_default_budget_is_half_the_detected_limit(monkeypatch):
    monkeypatch.setattr(_hostmem, "detected_limit_bytes", lambda: 62 * GB)
    assert _hostmem.budget_bytes() == 31 * GB


def test_absolute_env_override_wins(monkeypatch):
    monkeypatch.setattr(_hostmem, "detected_limit_bytes", lambda: 512 * GB)
    monkeypatch.setenv(_hostmem.BUDGET_BYTES_ENV, str(8 * GB))
    assert _hostmem.budget_bytes() == 8 * GB


def test_zero_bytes_disables_the_bound(monkeypatch):
    """An escape hatch for a caller who knows better than we do."""
    monkeypatch.setenv(_hostmem.BUDGET_BYTES_ENV, "0")
    assert _hostmem.budget_bytes() is None


def test_fraction_env_override(monkeypatch):
    monkeypatch.setattr(_hostmem, "detected_limit_bytes", lambda: 100 * GB)
    monkeypatch.setenv(_hostmem.BUDGET_FRACTION_ENV, "0.25")
    assert _hostmem.budget_bytes() == 25 * GB


@pytest.mark.parametrize("value", ["not-a-number", "-1"])
def test_bad_absolute_override_raises(monkeypatch, value):
    monkeypatch.setenv(_hostmem.BUDGET_BYTES_ENV, value)
    with pytest.raises(_hostmem.SnoutyHostMemoryError):
        _hostmem.budget_bytes()


@pytest.mark.parametrize("value", ["nope", "0", "1.5", "-0.2"])
def test_bad_fraction_override_raises(monkeypatch, value):
    monkeypatch.setenv(_hostmem.BUDGET_FRACTION_ENV, value)
    with pytest.raises(_hostmem.SnoutyHostMemoryError):
        _hostmem.budget_bytes()


def test_no_bound_when_nothing_is_detectable(monkeypatch):
    monkeypatch.setattr(_hostmem, "detected_limit_bytes", lambda: None)
    assert _hostmem.budget_bytes() is None


# --- the bound itself --------------------------------------------------------


def _peak_concurrent(reservations: list[int], hold_s: float = 0.05) -> int:
    """Run one thread per reservation and report how many held the budget at
    the same moment."""
    lock = threading.Lock()
    live = 0
    peak = 0

    def worker(nbytes: int) -> None:
        nonlocal live, peak
        with _hostmem.reserve(nbytes):
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(hold_s)
            with lock:
                live -= 1

    threads = [threading.Thread(target=worker, args=(n,)) for n in reservations]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a reservation deadlocked"
    return peak


def test_concurrency_is_capped_by_the_budget(monkeypatch):
    """Eight threads, a 10-byte budget, 4 bytes each. Two fit, so the other
    six wait. Without the bound all eight would run at once."""
    monkeypatch.setenv(_hostmem.BUDGET_BYTES_ENV, "10")
    _hostmem.reset()
    assert _peak_concurrent([4] * 8) <= 2


def test_transforms_are_unbounded_when_no_budget_resolves(monkeypatch):
    """Proves the cap in the previous test comes from the budget and is not
    an artifact of thread timing."""
    monkeypatch.setattr(_hostmem, "detected_limit_bytes", lambda: None)
    _hostmem.reset()
    assert _peak_concurrent([4] * 8) > 2


def test_a_task_larger_than_the_whole_budget_still_runs(monkeypatch):
    """A 40-byte task against a 10-byte budget must run alone rather than
    wait forever. Clamping the request is what prevents the deadlock."""
    monkeypatch.setenv(_hostmem.BUDGET_BYTES_ENV, "10")
    _hostmem.reset()
    assert _peak_concurrent([40, 40, 40]) == 1


def test_mixed_geometries_share_one_budget(monkeypatch):
    """The session-convert case. One large scene and several small ones draw
    from the same budget, so the small ones are not bounded by the large
    one's footprint."""
    monkeypatch.setenv(_hostmem.BUDGET_BYTES_ENV, "10")
    _hostmem.reset()
    assert _peak_concurrent([8] + [1] * 6) <= 7


def test_the_budget_is_released_after_each_reservation(monkeypatch):
    monkeypatch.setenv(_hostmem.BUDGET_BYTES_ENV, "10")
    _hostmem.reset()
    for _ in range(5):
        with _hostmem.reserve(10):
            pass
    with _hostmem.reserve(10):
        pass


def test_a_raising_transform_releases_its_reservation(monkeypatch):
    """A failed timepoint must not leak budget, or a long convert starves."""
    monkeypatch.setenv(_hostmem.BUDGET_BYTES_ENV, "10")
    _hostmem.reset()
    with pytest.raises(RuntimeError):
        with _hostmem.reserve(10):
            raise RuntimeError("transform blew up")
    assert _peak_concurrent([10]) == 1


# --- the reader honours the bound -------------------------------------------


class _ConcurrencySpy:
    """Wraps a transform and records how many calls overlapped."""

    def __init__(self, wrapped):
        self._wrapped = wrapped
        self._lock = threading.Lock()
        self._live = 0
        self.peak = 0
        self.calls = 0

    def __call__(self, *args, **kwargs):
        with self._lock:
            self._live += 1
            self.calls += 1
            self.peak = max(self.peak, self._live)
        try:
            time.sleep(0.02)
            return self._wrapped(*args, **kwargs)
        finally:
            with self._lock:
                self._live -= 1


# Synthetic fixture geometry: size_z=4, cropped size_y=6, size_x=8,
# scan_step_size_px=7.0, one channel, uint16.
#   raw       (4,  6, 8) =  192 voxels
#   desheared (4, 27, 8) =  864 voxels   (6 + round(7 * 3) = 27)
# desheared footprint = (192 + 864) * 1 channel * 2 bytes = 2112 bytes
FIXTURE_DESHEARED_FOOTPRINT = 2112


def test_reader_footprint_matches_the_fixture_geometry(synthetic_snouty):
    """Guards the literal the concurrency tests below budget against. If the
    shape formulas move, this fails before the cap tests turn misleading."""
    reader = SnoutyReader(synthetic_snouty.dir, mode="desheared")
    assert reader.transform_footprint_bytes == FIXTURE_DESHEARED_FOOTPRINT


def test_reader_caps_concurrent_transforms(tmp_path, monkeypatch):
    """The issue's headline criterion. Sixteen timepoints, eight scheduler
    threads, a budget that fits two transforms. No more than two run at once,
    whatever the core count."""
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=16)
    monkeypatch.setenv(_hostmem.BUDGET_BYTES_ENV, str(2 * FIXTURE_DESHEARED_FOOTPRINT))
    _hostmem.reset()
    spy = _ConcurrencySpy(_deshear.deshear_czyx)
    monkeypatch.setattr(_deshear, "deshear_czyx", spy)

    reader = SnoutyReader(fixture.dir, mode="desheared")
    with dask.config.set(scheduler="threads", num_workers=8):
        reader.xarray_dask_data.compute()

    assert spy.calls == 16
    assert spy.peak <= 2


def test_transforms_run_wide_when_the_bound_is_disabled(tmp_path, monkeypatch):
    """Proves the cap above comes from the budget, not from the scheduler
    failing to parallelize the fixture in the first place."""
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=16)
    monkeypatch.setenv(_hostmem.BUDGET_BYTES_ENV, "0")
    _hostmem.reset()
    spy = _ConcurrencySpy(_deshear.deshear_czyx)
    monkeypatch.setattr(_deshear, "deshear_czyx", spy)

    reader = SnoutyReader(fixture.dir, mode="desheared")
    with dask.config.set(scheduler="threads", num_workers=8):
        reader.xarray_dask_data.compute()

    assert spy.peak > 2


def test_raw_mode_is_not_bounded(tmp_path, monkeypatch):
    """``raw`` runs no transform and holds 0.73 GB per task, so it reserves
    nothing and keeps the scheduler's full width."""
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=4)
    monkeypatch.setenv(_hostmem.BUDGET_BYTES_ENV, "1")
    _hostmem.reset()

    reader = SnoutyReader(fixture.dir, mode="raw")
    assert reader.transform_footprint_bytes == 0
    with dask.config.set(scheduler="threads", num_workers=8):
        result = reader.xarray_dask_data.compute()
    assert result.shape == (4, 1, 4, 6, 8)


def test_bounded_and_unbounded_output_is_identical(tmp_path, monkeypatch):
    """The bound changes scheduling only. It must never change pixels."""
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=4)

    monkeypatch.setenv(_hostmem.BUDGET_BYTES_ENV, "0")
    _hostmem.reset()
    unbounded = SnoutyReader(fixture.dir, mode="traditional").xarray_dask_data.compute()

    monkeypatch.setenv(_hostmem.BUDGET_BYTES_ENV, str(FIXTURE_DESHEARED_FOOTPRINT))
    _hostmem.reset()
    bounded = SnoutyReader(fixture.dir, mode="traditional").xarray_dask_data.compute()

    np.testing.assert_array_equal(bounded.values, unbounded.values)


def test_host_memory_error_is_catchable_as_a_snouty_error(monkeypatch):
    """A caller that wraps a convert in ``except SnoutyError`` must catch a
    bad budget value too, not just a bad mode."""
    from zarrmony_snouty.adapter import SnoutyError, SnoutyHostMemoryError

    assert issubclass(SnoutyHostMemoryError, SnoutyError)
    monkeypatch.setenv(_hostmem.BUDGET_BYTES_ENV, "twelve")
    with pytest.raises(SnoutyError):
        _hostmem.budget_bytes()
