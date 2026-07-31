"""Reader for a top-level Snouty GUI-session directory.

A Snouty GUI session (directory whose name ends in ``_ht_sols_gui``) holds
one or more acquisition subdirectories plus session-scoped position files.
This reader composes one lightweight :class:`SnoutyReader` per non-empty
subdir and exposes their scenes flat-concatenated so a single
``zarrmony convert`` call fans out to N output stores.

Child readers are instantiated lazily — the session reader only touches
each subdir enough to enumerate scene names (shallow validation, listing
``data/*.tif`` and grouping by position). Full metadata parsing happens on
first ``set_scene()`` that reaches that child. Errors raised by that lazy
parse (missing sidecar fields, ``volumes_per_buffer > 1``, malformed XY
list, etc.) propagate normally — by the time the caller has committed to
reading a scene, warn-and-continue would silently drop pixel data.

Subdirs that fail the shallow ``data/`` + ``metadata/`` check emit a
:class:`SnoutySubdirSkippedWarning` at ``__init__`` time and are dropped
from ``scenes``. A session with **zero** surviving children raises
:class:`SnoutyDataError` — a batch conversion that produces no output is a
hard error, not a silent zero-output run.

``XY_stage_position_list.txt`` sits at the session level in the observed
fixture and has one row per XY position that the session's multi-position
children scan. When the list is present, each multi-position child whose
position count equals the list length surfaces per-position stage
coordinates on its scenes; children with a mismatched count emit one
:class:`SnoutySessionLayoutWarning` per child at ``__init__`` time and
have their stage attrs omitted. Sessions without the file omit attrs
silently — some GUI runs never move the stage.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Literal

import numpy as np
import xarray as xr

from .adapter import (
    _MODES,
    _XY_POSITION_LIST_FILENAME,
    Mode,
    SnoutyDataError,
    SnoutyModeError,
    SnoutyReader,
    _group_by_position,
    _load_xy_position_list,
    _PixelSizes,
)

__all__ = [
    "SnoutySessionLayoutWarning",
    "SnoutySessionReader",
    "SnoutySubdirSkippedWarning",
]


_SUBDIR_SUFFIXES = ("_ht_sols_snap", "_ht_sols_acquire")

# Machine-parseable reason tokens surfaced on SnoutySubdirSkippedWarning
# messages. Tests grep for these to assert the skip reason without depending
# on the exact prose. Keep the token set stable — downstream tooling filters
# on them.
_SkipReason = Literal[
    "missing_data_dir",
    "missing_metadata_dir",
    "empty_data",
    "no_metadata",
]


class SnoutySubdirSkippedWarning(UserWarning):
    """A ``_ht_sols_*`` child under a GUI-session directory failed shallow
    validation and was dropped from the session's scene list.

    Message format is ``"<subdir-path>: <reason-token>"`` where the reason
    token is one of ``missing_data_dir``, ``missing_metadata_dir``,
    ``empty_data``, ``no_metadata``. Downstream tooling can filter on the
    token verbatim.
    """


class SnoutySessionLayoutWarning(UserWarning):
    """The session-level ``XY_stage_position_list.txt`` is present but its
    length does not equal a multi-position child's position count, so stage
    XY attrs are omitted for that child.

    Emitted once per mismatched child so a session whose global-scan-order
    list happens to match some subdirs and not others produces one clear
    warning per skipped subdir.
    """


def _shallow_validate(subdir: Path) -> _SkipReason | None:
    """Return ``None`` if the subdir looks like a valid Snouty acquisition,
    or a machine-parseable reason token if it doesn't.

    Cheap by design: at most one ``iterdir()`` per subdir check. No metadata
    parsing, no ``.tif`` header reads — those happen lazily when the caller
    actually opens the scene.
    """
    data_dir = subdir / "data"
    metadata_dir = subdir / "metadata"
    if not data_dir.is_dir():
        return "missing_data_dir"
    if not metadata_dir.is_dir():
        return "missing_metadata_dir"
    if not any(p.is_file() and p.suffix == ".tif" for p in data_dir.iterdir()):
        return "empty_data"
    if not any(p.is_file() and p.suffix == ".txt" for p in metadata_dir.iterdir()):
        return "no_metadata"
    return None


class SnoutySessionReader:
    """Reader-protocol adapter for a Snouty GUI-session directory.

    Composes one :class:`SnoutyReader` per surviving ``_ht_sols_*`` subdir
    and exposes the flat concatenation of every child's ``scenes`` list
    (verbatim — no session-level prefix, subdir names already carry the
    disambiguating information).
    """

    layout_hint = "flat"
    plate_layout = None

    def __init__(self, path: Path, mode: Mode = "raw") -> None:
        if mode not in _MODES:
            raise SnoutyModeError(
                f"unknown SnoutyReader mode {mode!r}; expected one of {list(_MODES)}"
            )
        self._dir = Path(path)
        self._mode: Mode = mode

        # Session-level XY list — one shared file for every multi-position
        # child in this GUI session. Length-checked per-child below.
        self._xy_positions = _load_xy_position_list(self._dir / _XY_POSITION_LIST_FILENAME)

        # Enumerate immediate _ht_sols_* children in a stable order so scene
        # indices are reproducible across runs.
        candidates = sorted(
            p
            for p in self._dir.iterdir()
            if p.is_dir() and any(p.name.endswith(s) for s in _SUBDIR_SUFFIXES)
        )

        # Shallow-validate; emit one warning per skipped subdir. The message
        # names the subdir path and the reason token so pytest.warns can
        # match on the token verbatim.
        surviving: list[Path] = []
        for subdir in candidates:
            reason = _shallow_validate(subdir)
            if reason is None:
                surviving.append(subdir)
            else:
                warnings.warn(
                    f"{subdir}: {reason}",
                    SnoutySubdirSkippedWarning,
                    stacklevel=2,
                )

        if not surviving:
            raise SnoutyDataError(
                f"{self._dir}: no valid _ht_sols_* subdirectories found "
                "after shallow validation; refusing to produce a zero-output "
                "conversion"
            )

        # Cheap per-child scene enumeration: list data/*.tif, group by
        # position via the same regex the child adapter uses, apply the
        # suffix-only-when-needed naming rule. No metadata read, no image
        # decode — full instantiation is deferred to _ensure_child().
        self._children_dirs: list[Path] = []
        self._child_scenes_files: list[list] = []
        self._child_xy_positions: list[list[tuple[float, float]] | None] = []
        self.scenes: list[str] = []
        # scene_map[i] = (child_index, per_child_scene_index)
        self._scene_map: list[tuple[int, int]] = []
        self._readers: list[SnoutyReader | None] = []

        for subdir in surviving:
            data_files = sorted(
                (subdir / "data").glob("*.tif"),
                key=lambda p: p.stat().st_mtime,
            )
            grouped = _group_by_position(data_files, subdir / "data")
            is_single_position = len(grouped) == 1 and grouped[0][0] is None
            if is_single_position:
                child_scenes = [subdir.name]
            else:
                child_scenes = [f"{subdir.name}__p{i:06d}" for i, _ in grouped]

            child_xy = self._resolve_child_xy(subdir, is_single_position, len(grouped))

            child_idx = len(self._children_dirs)
            self._children_dirs.append(subdir)
            self._child_scenes_files.append(grouped)
            self._child_xy_positions.append(child_xy)
            self._readers.append(None)
            for per_idx, scene_name in enumerate(child_scenes):
                self.scenes.append(scene_name)
                self._scene_map.append((child_idx, per_idx))

        self._active = 0

    def _resolve_child_xy(
        self, subdir: Path, is_single_position: bool, n_positions: int
    ) -> list[tuple[float, float]] | None:
        """Decide which XY position list (if any) to hand to a child reader.

        - Session has no list → child gets ``None`` (attrs omitted silently).
        - Child is single-position → child gets ``None`` (attrs unused anyway;
          keeps the intent explicit).
        - Session list length matches child's position count → child gets
          the list verbatim (attrs surfaced per-position).
        - Session list length mismatches → warn once for this child, hand
          ``None`` (attrs omitted, warning explains why).
        """
        if self._xy_positions is None:
            return None
        if is_single_position:
            return None
        if len(self._xy_positions) == n_positions:
            return self._xy_positions
        warnings.warn(
            f"{subdir}: session XY_stage_position_list.txt has "
            f"{len(self._xy_positions)} entries but this subdir has "
            f"{n_positions} positions — omitting stage.xy_mm attrs",
            SnoutySessionLayoutWarning,
            stacklevel=3,
        )
        return None

    def set_scene(self, index: int) -> None:
        if not 0 <= index < len(self.scenes):
            raise IndexError(
                f"scene index {index} out of range; valid indices are "
                f"0..{len(self.scenes) - 1}"
            )
        self._active = index
        # Forward to the target child eagerly so the child's active-scene
        # cursor tracks the session's; subsequent property access is a
        # simple delegation.
        child_idx, per_idx = self._scene_map[index]
        self._ensure_child(child_idx).set_scene(per_idx)

    def _ensure_child(self, child_idx: int) -> SnoutyReader:
        reader = self._readers[child_idx]
        if reader is None:
            reader = SnoutyReader(
                self._children_dirs[child_idx],
                mode=self._mode,
                xy_positions=self._child_xy_positions[child_idx],
            )
            self._readers[child_idx] = reader
        return reader

    def _active_child(self) -> SnoutyReader:
        child_idx, per_idx = self._scene_map[self._active]
        reader = self._ensure_child(child_idx)
        # Idempotent: keeps the child's cursor in sync even if a caller
        # mutates _active without going through set_scene (defensive).
        reader.set_scene(per_idx)
        return reader

    @property
    def xarray_dask_data(self) -> xr.DataArray:
        return self._active_child().xarray_dask_data

    @property
    def physical_pixel_sizes(self) -> _PixelSizes:
        return self._active_child().physical_pixel_sizes

    @property
    def channel_names(self) -> list[str]:
        return self._active_child().channel_names

    @property
    def dtype(self) -> np.dtype:
        return self._active_child().dtype

    @property
    def metadata(self) -> str:
        return self._active_child().metadata

    def close(self) -> None:
        for reader in self._readers:
            if reader is not None:
                reader.close()
