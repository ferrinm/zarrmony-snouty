"""Reader Protocol adapter for a single Snouty acquisition subdirectory.

Handles single-position, single-channel acquisitions with one or more
timepoints. Multiple ``.tif`` files under ``data/`` are concatenated along
the T axis (one dask chunk per timepoint). ``volumes_per_buffer > 1`` (the
vendor's hardware-limited time sampling — multiple volumes stacked inside
one ``.tif``) is a real shape verified against a fixture but not yet
implemented because our only real fixture also has multiple channels; it
raises ``SnoutyVolumesPerBufferUnsupportedError`` until the multi-channel
path (#4) lands and the composition can be tested end-to-end.
Multi-position (``_p<pos>.tif`` naming) and multi-channel
(``channels_per_slice`` with more than one entry) raise ``NotImplementedError``
subclasses pointing at the v0.2 trackers so users get an actionable error
rather than a silently wrong output.

Three output modes are available via the ``mode`` kwarg (default ``"raw"``
preserves v0.1 behavior). ``"desheared"`` and ``"traditional"`` port the CPU
paths of ``snouty_folder.SnoutyFolder`` (see Austin Lefebvre's
``snouty-folder`` package at https://github.com/aelefebv/snouty-folder) —
see :mod:`zarrmony_snouty._deshear`.
"""

from __future__ import annotations

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
    """The acquisition directory has no readable data files."""


class SnoutyMultipositionUnsupportedError(SnoutyError, NotImplementedError):
    """The acquisition contains multiple stage positions; v0.1 handles only one.

    Snouty writes multi-position acquisitions as ``NNNNNN_pMMMMMM.tif`` files
    (position index in the ``_p...`` segment). Tracked for v0.2.
    """


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
    """The acquisition uses more than one channel; v0.1 handles only one.

    ``channels_per_slice`` in the metadata sidecar lists more than one label.
    Tracked for v0.2.
    """


class SnoutyModeError(SnoutyError, ValueError):
    """The ``mode`` kwarg (or ``ZARRMONY_SNOUTY_MODE`` env var) is not one of
    ``raw`` / ``desheared`` / ``traditional``."""


__all__ = [
    "SnoutyDataError",
    "SnoutyError",
    "SnoutyModeError",
    "SnoutyMultiChannelUnsupportedError",
    "SnoutyMultipositionUnsupportedError",
    "SnoutyReader",
    "SnoutyVolumesPerBufferUnsupportedError",
]


@dataclass(frozen=True)
class _PixelSizes:
    X: float | None
    Y: float | None
    Z: float | None


_POSITION_TIF_RE = re.compile(r"_p\d+\.tif$", re.IGNORECASE)


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
        self._data_files = sorted(
            (self._dir / "data").glob("*.tif"),
            key=lambda p: p.stat().st_mtime,
        )
        if not self._data_files:
            raise SnoutyDataError(f"no .tif files in {self._dir / 'data'}")

        _validate_v01_scope(self._data_files, self._meta)

        self.scenes: list[str] = [self._dir.name]
        self._active = 0

    def set_scene(self, index: int) -> None:
        if index != 0:
            raise IndexError(f"scene index {index} out of range; only scene 0 exists")
        self._active = 0

    @property
    def xarray_dask_data(self) -> xr.DataArray:
        m = self._meta
        shape_zyx = self._output_shape_zyx()
        # dtype matches the vendor's PCO output (16-bit) — same assumption
        # snouty-folder makes when writing its OME-TIFFs.
        volumes = [
            da.from_delayed(self._delayed_volume(path), shape=shape_zyx, dtype="uint16")
            for path in self._data_files
        ]
        stacked = da.stack(volumes, axis=0)  # (T, Z, Y, X)
        stacked = stacked[:, None, :, :, :]  # → (T, C=1, Z, Y, X)
        return xr.DataArray(
            stacked,
            dims=("T", "C", "Z", "Y", "X"),
            coords={"C": list(m.channels)},
        )

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


def _validate_v01_scope(data_files: list[Path], meta: SnoutyMetadata) -> None:
    if any(_POSITION_TIF_RE.search(p.name) for p in data_files):
        raise SnoutyMultipositionUnsupportedError(
            "Snouty multi-position acquisitions (files named _pNNNNNN.tif) are "
            "not supported in zarrmony-snouty v0.2. Tracked for v0.3 (#3)."
        )
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
