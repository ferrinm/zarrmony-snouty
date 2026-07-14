"""Reader Protocol adapter for a single Snouty acquisition subdirectory.

v0.1 handles the simplest observed shape: one ``_snap`` or ``_acquire``
subdirectory with a single-position, single-timepoint, single-channel raw
skewed volume (Z, Y, X). Everything else — multi-position (``_p<pos>.tif``
naming), multi-timepoint (multiple ``.tif`` files), multi-channel
(``channels_per_slice`` with more than one entry) — raises a
``NotImplementedError`` subclass with a pointer at the v0.2 tracker so users
get an actionable error rather than a silently wrong output.

Deshear/traditional-view modes are intentionally out of scope for v0.1; the
reference algorithm lives in ``snouty_folder.SnoutyFolder`` (see the
``snouty-folder`` package by Austin Lefebvre) and will be ported in v0.2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import dask
import dask.array as da
import tifffile
import xarray as xr

from ._metadata import SnoutyMetadata, parse_metadata_dir


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


__all__ = [
    "SnoutyDataError",
    "SnoutyError",
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

    def __init__(self, path: Path) -> None:
        self._dir = Path(path)
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
        delayed = dask.delayed(_read_and_crop_plane)(data_path, m.timestamp_strip_px)
        # dtype matches the vendor's PCO output (16-bit) — same assumption
        # snouty-folder makes when writing its OME-TIFFs.
        volume = da.from_delayed(delayed, shape=(m.size_z, m.size_y, m.size_x), dtype="uint16")
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
        # X and Y are the sample-plane pixel size. Z is the scan step, i.e.
        # the raw skewed-Z spacing between successive slices — NOT the
        # de-sheared/rotated orthogonal Z. Downstream tools that need real
        # geometry will deshear (v0.2) or apply the vendor's `voxel_aspect_ratio`.
        return _PixelSizes(X=m.sample_px_um, Y=m.sample_px_um, Z=m.scan_step_size_um)

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
