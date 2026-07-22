"""Reader Protocol adapter for a single Snouty acquisition subdirectory.

Handles the simplest observed shape: one ``_snap`` or ``_acquire``
subdirectory with a single-position, single-timepoint, single-channel raw
skewed volume (Z, Y, X). Everything else — multi-position (``_p<pos>.tif``
naming), multi-timepoint (multiple ``.tif`` files), multi-channel
(``channels_per_slice`` with more than one entry) — raises a
``NotImplementedError`` subclass with a pointer at the v0.2 tracker so users
get an actionable error rather than a silently wrong output.

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


class SnoutyMultiTimepointUnsupportedError(SnoutyError, NotImplementedError):
    """The acquisition contains multiple timepoints; v0.1 handles only one.

    Snouty writes multi-timepoint acquisitions as multiple ``NNNNNN.tif`` files
    (one per volume). Tracked for v0.2.
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
    "SnoutyMultiTimepointUnsupportedError",
    "SnoutyMultipositionUnsupportedError",
    "SnoutyReader",
]


@dataclass(frozen=True)
class _PixelSizes:
    X: float | None
    Y: float | None
    Z: float | None


_POSITION_TIF_RE = re.compile(r"_p\d+\.tif$", re.IGNORECASE)


def _read_and_crop_plane(path: str, timestamp_strip_px: int):
    """Read a full Snouty volume TIFF and crop the PCO timestamp strip.

    Snouty TIFFs are (Z, Y, X) with the top ``timestamp_strip_px`` rows of
    every Y slice reserved for a binary-coded-decimal timestamp. Cropping it
    matches what ``snouty-folder`` does before writing OME-TIFF.
    """
    volume = tifffile.imread(path)
    return volume[..., timestamp_strip_px:, :]


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
        data_path = str(self._data_files[0])
        delayed_raw = dask.delayed(_read_and_crop_plane)(data_path, m.timestamp_strip_px)
        # dtype matches the vendor's PCO output (16-bit) — same assumption
        # snouty-folder makes when writing its OME-TIFFs.
        if self._mode == "raw":
            shape_zyx = (m.size_z, m.size_y, m.size_x)
            delayed_vol = delayed_raw
        elif self._mode == "desheared":
            shape_zyx = _deshear.desheared_shape(m.size_z, m.size_y, m.size_x, m.scan_step_size_px)
            delayed_vol = dask.delayed(_deshear.deshear_zyx)(delayed_raw, m.scan_step_size_px)
        else:  # traditional
            shape_zyx = _deshear.traditional_shape(
                m.size_z, m.size_y, m.size_x, m.scan_step_size_px, m.voxel_aspect_ratio
            )
            delayed_vol = dask.delayed(_deshear.traditional_zyx)(
                delayed_raw, m.scan_step_size_px, m.voxel_aspect_ratio
            )
        volume = da.from_delayed(delayed_vol, shape=shape_zyx, dtype="uint16")
        # (Z, Y, X) → (T=1, C=1, Z, Y, X)
        volume = volume[None, None, :, :, :]
        return xr.DataArray(
            volume,
            dims=("T", "C", "Z", "Y", "X"),
            coords={"C": list(m.channels)},
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
            "not supported in zarrmony-snouty v0.1. Tracked for v0.2."
        )
    if len(data_files) > 1:
        raise SnoutyMultiTimepointUnsupportedError(
            f"Snouty multi-timepoint acquisitions ({len(data_files)} .tif files "
            f"in data/) are not supported in zarrmony-snouty v0.1. Tracked for v0.2."
        )
    if len(meta.channels) > 1:
        raise SnoutyMultiChannelUnsupportedError(
            f"Snouty multi-channel acquisitions "
            f"(channels_per_slice={meta.channels!r}) are not supported in "
            "zarrmony-snouty v0.1. Tracked for v0.2."
        )
    if meta.size_t != 1:
        raise SnoutyMultiTimepointUnsupportedError(
            f"volumes_per_buffer={meta.size_t} implies multi-timepoint output; "
            "zarrmony-snouty v0.1 only handles single-volume acquisitions. "
            "Tracked for v0.2."
        )
