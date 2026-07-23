"""Reader Protocol adapter for a single Snouty acquisition subdirectory.

Handles single- and multi-position, single-channel acquisitions with one or
more timepoints. Files under ``data/`` are grouped by position (the
``MMMMMM`` in ``NNNNNN_pMMMMMM.tif``) into one scene per position; within
each scene, files are concatenated along the T axis (one dask chunk per
timepoint). Single-position acquisitions (no ``_pNNNNNN.tif`` files) expose
one scene named after the acquisition directory, preserving v0.1 behavior.
Multi-position acquisitions expose scenes named
``<acquisition-dir>__p<zero-padded-index>`` (the double underscore is the
intentional boundary separator so the suffix does not collide with the
vendor's single-underscore filename fragments).

``volumes_per_buffer > 1`` (the vendor's hardware-limited time sampling —
multiple volumes stacked inside one ``.tif``) is a real shape verified
against a fixture but not yet implemented because our only real fixture
also has multiple channels; it raises
``SnoutyVolumesPerBufferUnsupportedError`` until the multi-channel path
(#4) lands and the composition can be tested end-to-end. Multi-channel
(``channels_per_slice`` with more than one entry) raises
``SnoutyMultiChannelUnsupportedError`` for the same reason.

Three output modes are available via the ``mode`` kwarg (default ``"raw"``
preserves v0.1 behavior). ``"desheared"`` and ``"traditional"`` port the CPU
paths of ``snouty_folder.SnoutyFolder`` (see Austin Lefebvre's
``snouty-folder`` package at https://github.com/aelefebv/snouty-folder) —
see :mod:`zarrmony_snouty._deshear`.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import dask
import dask.array as da
import numpy as np
import tifffile
import xarray as xr

from . import _deshear
from ._metadata import SnoutyMetadata, parse_metadata_dir

Mode = Literal["raw", "desheared", "traditional"]
_MODES: tuple[Mode, ...] = ("raw", "desheared", "traditional")


class SnoutyError(Exception):
    """Base class for zarrmony-snouty errors."""


class SnoutyDataError(SnoutyError):
    """The acquisition directory has no readable data files, or the files
    mix multi-position (``_pNNNNNN.tif``) and non-position naming."""


class SnoutyVolumesPerBufferUnsupportedError(SnoutyError, NotImplementedError):
    """The sidecar reports ``volumes_per_buffer > 1``.

    Snouty's hardware-limited time sampling packs multiple volumes into a
    single ``.tif`` (frames laid out as
    ``(volumes_per_buffer, slices_per_volume, channels, Y, X)``). The math
    (``size_t = volumes_per_buffer * len(data_files)``) is verified against
    real data, but our only real fixture also has multiple channels, so we
    hold this shape off until the multi-channel path (#4) lands and the
    composition can be tested end-to-end.
    """


class SnoutyMultiChannelUnsupportedError(SnoutyError, NotImplementedError):
    """The acquisition uses more than one channel; v0.2 handles only one.

    ``channels_per_slice`` in the metadata sidecar lists more than one label.
    Tracked for v0.3 (#4).
    """


class SnoutyModeError(SnoutyError, ValueError):
    """The ``mode`` kwarg (or ``ZARRMONY_SNOUTY_MODE`` env var) is not one of
    ``raw`` / ``desheared`` / ``traditional``."""


class SnoutyXYPositionListError(SnoutyError, ValueError):
    """The parent GUI-session directory's ``XY_stage_position_list.txt`` is
    malformed (unparseable line, wrong arity, or non-numeric values)."""


__all__ = [
    "SnoutyDataError",
    "SnoutyError",
    "SnoutyModeError",
    "SnoutyMultiChannelUnsupportedError",
    "SnoutyReader",
    "SnoutyVolumesPerBufferUnsupportedError",
    "SnoutyXYPositionListError",
]


@dataclass(frozen=True)
class _PixelSizes:
    X: float | None
    Y: float | None
    Z: float | None


_POSITION_TIF_RE = re.compile(r"^(?P<t>\d+)_p(?P<p>\d+)\.tif$", re.IGNORECASE)
_XY_POSITION_LIST_FILENAME = "XY_stage_position_list.txt"


def _read_and_crop_plane(path: str, timestamp_strip_px: int):
    """Read a Snouty volume TIFF for a single timepoint and crop the PCO strip.

    tifffile may return ``(Z, Y, X)`` or ``(Z, 1, Y, X)`` depending on whether
    the vendor tagged a singleton C axis; both squeeze to ``(Z, Y, X)``. The
    top ``timestamp_strip_px`` rows of every Y slice hold the PCO
    binary-coded-decimal timestamp — cropping matches what ``snouty-folder``
    does before writing OME-TIFF.
    """
    volume = tifffile.imread(path)
    volume = np.squeeze(volume)
    if volume.ndim != 3:
        raise SnoutyDataError(
            f"expected a single-channel (Z, Y, X) volume in {path}; got shape {volume.shape}"
        )
    return volume[:, timestamp_strip_px:, :]


class SnoutyReader:
    layout_hint = "flat"
    plate_layout = None

    def __init__(self, path: Path, mode: Mode = "raw") -> None:
        if mode not in _MODES:
            raise SnoutyModeError(
                f"unknown SnoutyReader mode {mode!r}; expected one of {list(_MODES)}"
            )
        self._dir = Path(path)
        self._mode: Mode = mode
        self._meta: SnoutyMetadata = parse_metadata_dir(self._dir / "metadata")
        all_files = sorted(
            (self._dir / "data").glob("*.tif"),
            key=lambda p: p.stat().st_mtime,
        )
        if not all_files:
            raise SnoutyDataError(f"no .tif files in {self._dir / 'data'}")

        _validate_v01_scope(self._meta)

        # Group by position index; None means a non-``_p`` (legacy single-position) file.
        self._scenes_files: list[tuple[int | None, list[Path]]] = _group_by_position(
            all_files, self._dir / "data"
        )
        if len(self._scenes_files) == 1 and self._scenes_files[0][0] is None:
            self.scenes: list[str] = [self._dir.name]
        else:
            self.scenes = [f"{self._dir.name}__p{i:06d}" for i, _ in self._scenes_files]

        self._xy_positions = _load_xy_position_list(self._dir.parent / _XY_POSITION_LIST_FILENAME)
        self._active = 0

    def set_scene(self, index: int) -> None:
        if not 0 <= index < len(self.scenes):
            raise IndexError(
                f"scene index {index} out of range; valid indices are 0..{len(self.scenes) - 1}"
            )
        self._active = index

    @property
    def xarray_dask_data(self) -> xr.DataArray:
        m = self._meta
        shape_zyx = self._output_shape_zyx()
        position_index, files = self._scenes_files[self._active]
        # dtype matches the vendor's PCO output (16-bit) — same assumption
        # snouty-folder makes when writing its OME-TIFFs.
        volumes = [
            da.from_delayed(self._delayed_volume(path), shape=shape_zyx, dtype="uint16")
            for path in files
        ]
        stacked = da.stack(volumes, axis=0)  # (T, Z, Y, X)
        stacked = stacked[:, None, :, :, :]  # → (T, C=1, Z, Y, X)
        return xr.DataArray(
            stacked,
            dims=("T", "C", "Z", "Y", "X"),
            coords={"C": list(m.channels)},
            attrs=self._scene_attrs(position_index),
        )

    def _scene_attrs(self, position_index: int | None) -> dict:
        if self._xy_positions is None or position_index is None:
            return {}
        if position_index >= len(self._xy_positions):
            raise SnoutyXYPositionListError(
                f"{self._dir.parent / _XY_POSITION_LIST_FILENAME} has "
                f"{len(self._xy_positions)} entries but position index "
                f"{position_index} is referenced by {self._dir / 'data'}"
            )
        x_mm, y_mm = self._xy_positions[position_index]
        return {"zarrmony": {"stage": {"xy_mm": [x_mm, y_mm]}}}

    def _output_shape_zyx(self) -> tuple[int, int, int]:
        m = self._meta
        if self._mode == "raw":
            return (m.size_z, m.size_y, m.size_x)
        if self._mode == "desheared":
            return _deshear.desheared_shape(m.size_z, m.size_y, m.size_x, m.scan_step_size_px)
        return _deshear.traditional_shape(
            m.size_z, m.size_y, m.size_x, m.scan_step_size_px, m.voxel_aspect_ratio
        )

    def _delayed_volume(self, path: Path):
        m = self._meta
        raw = dask.delayed(_read_and_crop_plane)(str(path), m.timestamp_strip_px)
        if self._mode == "raw":
            return raw
        if self._mode == "desheared":
            return dask.delayed(_deshear.deshear_zyx)(raw, m.scan_step_size_px)
        return dask.delayed(_deshear.traditional_zyx)(
            raw, m.scan_step_size_px, m.voxel_aspect_ratio
        )

    @property
    def physical_pixel_sizes(self) -> _PixelSizes:
        m = self._meta
        # X/Y are the sample-plane pixel size in every mode. Z differs:
        # - raw and desheared expose the vendor's scan step (deshear only
        #   aligns axes, it does not change spacing).
        # - traditional rotates into an orthogonal top-down view where Z
        #   spacing becomes sample_px_um * voxel_aspect_ratio.
        z = (
            m.sample_px_um * m.voxel_aspect_ratio
            if self._mode == "traditional"
            else m.scan_step_size_um
        )
        return _PixelSizes(X=m.sample_px_um, Y=m.sample_px_um, Z=z)

    @property
    def channel_names(self) -> list[str]:
        return [str(c) for c in self._meta.channels]

    @property
    def metadata(self) -> str:
        return self._meta.raw_text

    def close(self) -> None:
        pass


def _group_by_position(files: list[Path], data_dir: Path) -> list[tuple[int | None, list[Path]]]:
    """Group ``.tif`` files by position index encoded in ``NNNNNN_pMMMMMM.tif``.

    Files that do not match the pattern are grouped under ``None`` (legacy
    single-position shape). Mixing the two shapes in a single ``data/`` dir
    is rejected as a hard error — Snouty never writes such a mix, and letting
    it slide would silently drop timepoints from one of the scenes.
    """
    by_position: dict[int | None, list[Path]] = {}
    for f in files:
        match = _POSITION_TIF_RE.match(f.name)
        key = int(match.group("p")) if match is not None else None
        by_position.setdefault(key, []).append(f)

    if None in by_position and len(by_position) > 1:
        raise SnoutyDataError(
            f"{data_dir} mixes multi-position (_pNNNNNN.tif) and non-position "
            "files; refusing to guess how they map to scenes"
        )
    if None in by_position:
        return [(None, by_position[None])]
    # Sort by numeric position index so scene order is stable and matches the
    # XY_stage_position_list row order.
    return sorted(by_position.items(), key=lambda item: item[0])


def _load_xy_position_list(path: Path) -> list[tuple[float, float]] | None:
    """Parse the vendor's ``XY_stage_position_list.txt`` into a
    position-index-ordered list of ``(x_mm, y_mm)`` tuples.

    The vendor writes each row as a Python list literal followed by a
    trailing comma (so the whole file is one comma-separated Python list
    without the enclosing brackets), e.g.::

        [0.0578, 0.0015],
        [0.1751, -0.028],

    We accept that shape and also the trailing-comma-free form. Returns
    ``None`` when the file is absent; raises on malformed content.
    """
    if not path.is_file():
        return None
    positions: list[tuple[float, float]] = []
    for lineno, raw_line in enumerate(path.read_text().splitlines(), start=1):
        # Strip trailing comma so the vendor's line-per-row format parses as a
        # standalone [x, y] literal rather than a 1-tuple wrapping the list.
        line = raw_line.strip().rstrip(",").strip()
        if not line:
            continue
        try:
            xy = ast.literal_eval(line)
        except (ValueError, SyntaxError) as exc:
            raise SnoutyXYPositionListError(
                f"{path} line {lineno}: cannot parse {line!r} as an [x_mm, y_mm] pair: {exc}"
            ) from exc
        if not isinstance(xy, (list, tuple)) or len(xy) != 2:
            raise SnoutyXYPositionListError(
                f"{path} line {lineno}: expected an [x_mm, y_mm] pair, got {xy!r}"
            )
        try:
            positions.append((float(xy[0]), float(xy[1])))
        except (TypeError, ValueError) as exc:
            raise SnoutyXYPositionListError(
                f"{path} line {lineno}: non-numeric coordinate in {xy!r}: {exc}"
            ) from exc
    return positions


def _validate_v01_scope(meta: SnoutyMetadata) -> None:
    if len(meta.channels) > 1:
        raise SnoutyMultiChannelUnsupportedError(
            f"Snouty multi-channel acquisitions "
            f"(channels_per_slice={meta.channels!r}) are not supported in "
            "zarrmony-snouty v0.2. Tracked for v0.3 (#4)."
        )
    if meta.size_t != 1:
        raise SnoutyVolumesPerBufferUnsupportedError(
            f"volumes_per_buffer={meta.size_t} packs multiple volumes into a "
            "single .tif; this shape is understood but not yet implemented "
            "because every real fixture with volumes_per_buffer > 1 also has "
            "multiple channels — deferred until the multi-channel path (#4) "
            "lands and the composition can be tested end-to-end."
        )
