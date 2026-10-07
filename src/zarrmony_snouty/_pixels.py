"""The per-scene pixel path, shared by every Snouty reader.

One scene is a list of ``.tif`` files — one per timepoint — plus the sidecar
geometry that they share. This module turns that pair into a lazy
``(T, C, Z, Y, X)`` array. It orders the files along T, reads and crops each
volume, runs the transform the mode asks for, and reports the physical pixel
sizes.

:class:`~zarrmony_snouty.adapter.SnoutyReader` and
:class:`~zarrmony_snouty.plate.SnoutyPlateReader` differ only in how they
group files into scenes: the flat reader groups by ``_pNNNNNN`` position, the
plate reader groups by well and field. Everything downstream of the grouping
lives here, so the two readers cannot drift apart.
"""

from __future__ import annotations

import datetime as dt
import re
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import dask
import dask.array as da
import numpy as np
import tifffile
import xarray as xr
from ome_types.model import OME, Channel, Image, Pixels, PixelType

from . import _deshear, _deshear_gpu, _engine, _hostmem, _pco_timestamp
from ._engine import ResolvedEngine
from ._errors import SnoutyDataError, SnoutyVolumesPerBufferUnsupportedError
from ._metadata import SnoutyMetadata

Mode = Literal["raw", "desheared", "traditional"]
_MODES: tuple[Mode, ...] = ("raw", "desheared", "traditional")

#: The mode a caller gets when they name none. One constant, so the three
#: readers and the plugin shims cannot drift apart.
#:
#: ``desheared`` since v0.3, and the flip is breaking. It is the cheapest
#: mode that yields an axis-aligned volume: 0.15 s per timepoint, every input
#: voxel preserved exactly once, and the padding compresses to nothing. See
#: issue #11. ``traditional`` is not the default because it resamples with
#: nearest-neighbour and does not invert.
DEFAULT_MODE: Mode = "desheared"

#: Snouty PCO output is always 16-bit. One constant so the dask graph, the
#: ``dtype`` property, and the device-memory estimate cannot drift apart.
DTYPE = np.dtype("uint16")

_POSITION_TIF_RE = re.compile(r"^(?P<t>\d+)_p(?P<p>\d+)\.tif$", re.IGNORECASE)


class SnoutyTimestampWarning(UserWarning):
    """The burned-in camera timestamps did not order an acquisition's data
    files, so the reader fell back to the zero-padded filename order, or they
    ordered them differently from the filenames.

    The fallback is deterministic and correct for any acquisition Snouty wrote
    itself, so this warning reports a loss of the strongest ordering evidence
    rather than a loss of data.
    """


@dataclass(frozen=True)
class _PixelSizes:
    X: float | None
    Y: float | None
    Z: float | None


def _read_and_crop_plane(path: str, timestamp_strip_px: int):
    """Read a Snouty volume TIFF for a single timepoint and crop the PCO strip.

    Always returns ``(C, Z, Y, X)``. Single-channel files come back from
    tifffile as ``(Z, Y, X)`` or ``(Z, 1, Y, X)`` and get a C axis
    prepended; multi-channel files come back as ``(Z, C, Y, X)`` (Z
    outermost, matching the swap in ``snouty_folder.write_original_ome_tif``)
    and get the leading Z↔C axes swapped. The top ``timestamp_strip_px``
    rows of every Y slice hold the PCO binary-coded-decimal timestamp —
    cropping matches what ``snouty-folder`` does before writing OME-TIFF.
    """
    volume = tifffile.imread(path)
    if volume.ndim == 4 and volume.shape[1] == 1:
        volume = volume[:, 0, :, :]
    if volume.ndim == 3:
        volume = volume[np.newaxis, :, :, :]
    elif volume.ndim == 4:
        volume = np.swapaxes(volume, 0, 1)
    else:
        raise SnoutyDataError(
            f"expected a (Z, Y, X) or (Z, C, Y, X) volume in {path}; got shape {volume.shape}"
        )
    return volume[:, :, timestamp_strip_px:, :]


def _read_crop_and_transform(
    path: str,
    timestamp_strip_px: int,
    mode: Mode,
    scan_step_size_px: float,
    voxel_aspect_ratio: float,
    footprint_bytes: int,
    engine: ResolvedEngine,
):
    """Read, crop, and transform one timepoint under a host-memory reservation.

    The read and the transform are deliberately fused into a single dask task.
    Split across two tasks, the scheduler is free to materialize many input
    volumes before any transform reserves its budget, so the input term of the
    footprint would escape the bound. Fused, the whole peak sits inside the
    reservation. See :mod:`zarrmony_snouty._hostmem`.

    ``raw`` never reaches this function — it runs no transform, so it holds
    only the input volume and stays unbounded.

    ``engine`` is already resolved to ``cpu`` or ``gpu`` by the reader. There
    is no fallback here on purpose: a transient CUDA fault raises and fails
    the convert. A fallback inside a dask task cannot report itself — under a
    distributed scheduler it happens on a worker process and never reaches
    the client's reader object, so the audit would say ``gpu`` for pixels the
    CPU produced. See issue #10.
    """
    with _hostmem.reserve(footprint_bytes):
        volume = _read_and_crop_plane(path, timestamp_strip_px)
        if mode == "desheared":
            return _deshear.deshear_czyx(volume, scan_step_size_px)
        if engine == "gpu":
            return _deshear_gpu.traditional_czyx(volume, scan_step_size_px, voxel_aspect_ratio)
        return _deshear.traditional_czyx(volume, scan_step_size_px, voxel_aspect_ratio)


@dataclass(frozen=True)
class ScenePixels:
    """Everything a scene needs once its files are grouped.

    Holds the sidecar geometry, the output mode, and the resolved engine. The
    reader supplies the file list per scene, because that list is the one
    thing that differs between scenes of one acquisition.
    """

    meta: SnoutyMetadata
    mode: Mode
    engine: ResolvedEngine
    engine_fallback_reason: str | None = None

    def xarray(self, files: Sequence[Path], *, attrs: dict | None = None) -> xr.DataArray:
        """Build one scene's lazy ``(T, C, Z, Y, X)`` array from its files.

        ``files`` must already be in time order — see :func:`_order_data_files`.
        One dask chunk per timepoint.
        """
        shape_czyx = (len(self.meta.channels),) + self.output_shape_zyx
        # dtype matches the vendor's PCO output (16-bit) — same assumption
        # snouty-folder makes when writing its OME-TIFFs.
        volumes = [
            da.from_delayed(self._delayed_volume(path), shape=shape_czyx, dtype=DTYPE)
            for path in files
        ]
        stacked = da.stack(volumes, axis=0)  # (T, C, Z, Y, X)
        return xr.DataArray(
            stacked,
            dims=("T", "C", "Z", "Y", "X"),
            coords={"C": list(self.meta.channels)},
            attrs=attrs or {},
        )

    @property
    def output_shape_zyx(self) -> tuple[int, int, int]:
        m = self.meta
        if self.mode == "raw":
            return (m.size_z, m.size_y, m.size_x)
        if self.mode == "desheared":
            return _deshear.desheared_shape(m.size_z, m.size_y, m.size_x, m.scan_step_size_px)
        return _deshear.traditional_shape(
            m.size_z, m.size_y, m.size_x, m.scan_step_size_px, m.voxel_aspect_ratio
        )

    @property
    def physical_pixel_sizes(self) -> _PixelSizes:
        m = self.meta
        # X/Y are the sample-plane pixel size in every mode. Z differs:
        # - raw and desheared expose the vendor's scan step (deshear only
        #   aligns axes, it does not change spacing).
        # - traditional rotates into an orthogonal top-down view where Z
        #   spacing becomes sample_px_um * voxel_aspect_ratio.
        z = (
            m.sample_px_um * m.voxel_aspect_ratio
            if self.mode == "traditional"
            else m.scan_step_size_um
        )
        return _PixelSizes(X=m.sample_px_um, Y=m.sample_px_um, Z=z)

    @property
    def transform_footprint_bytes(self) -> int:
        """Peak host bytes one dask task holds while it transforms a timepoint.

        ``0`` in ``raw`` mode, which runs no transform. See
        :mod:`zarrmony_snouty._hostmem` for the bound this feeds.
        """
        m = self.meta
        return _hostmem.transform_footprint_bytes(
            mode=self.mode,
            size_z=m.size_z,
            size_y=m.size_y,
            size_x=m.size_x,
            n_channels=len(m.channels),
            scan_step_size_px=m.scan_step_size_px,
            voxel_aspect_ratio=m.voxel_aspect_ratio,
            itemsize=DTYPE.itemsize,
        )

    @property
    def channel_names(self) -> list[str]:
        return [str(c) for c in self.meta.channels]

    def ome_metadata(self, files: Sequence[Path], *, scene_index: int, scene_name: str) -> OME:
        """The OME description of one scene. Each reader exposes it as a
        zarrmony soft-optional ``ome_metadata`` property.

        zarrmony reads ``ome.images[0]`` and ignores everything after it, and
        it calls ``set_scene`` before it reads. So this returns exactly one
        ``Image``, for the scene the caller names, and never one per scene. A
        list would make every plate field report field zero.

        A reader that exposes nothing here still converts: zarrmony catches
        the ``AttributeError``, warns once per scene, writes the failure into
        the store, and substitutes a stub ``Image``. The stub costs the
        per-scene ``channels`` audit block and ``acquisition.date``
        (issue #39).

        Three values do not come from the sidecar, and each one is a silently
        wrong store if taken from it:

        - Z, Y and X come from :attr:`output_shape_zyx`. The sidecar records
          the raw shape, and the default ``desheared`` mode changes it.
        - T is the number of files in this scene. The sidecar
          ``volumes_per_buffer`` is forced to 1 by :func:`_validate_v01_scope`.
        - The physical sizes come from :attr:`physical_pixel_sizes`, which
          reports a different Z in ``traditional`` mode.

        ``objective`` and ``instruments`` stay unset. A Snouty sidecar records
        no objective, and the instrument fields zarrmony reads already arrive
        through :attr:`acquisition_audit`.
        """
        size_z, size_y, size_x = self.output_shape_zyx
        spacing = self.physical_pixel_sizes
        channels = self.channel_names
        pixels = Pixels(
            id=f"Pixels:{scene_index}",
            size_t=len(files),
            size_c=len(channels),
            size_z=size_z,
            size_y=size_y,
            size_x=size_x,
            physical_size_x=spacing.X,
            physical_size_y=spacing.Y,
            physical_size_z=spacing.Z,
            dimension_order="XYZCT",
            type=PixelType(DTYPE.name),
            channels=[
                Channel(id=f"Channel:{scene_index}:{index}", name=name)
                for index, name in enumerate(channels)
            ],
        )
        return OME(
            images=[
                Image(
                    id=f"Image:{scene_index}",
                    name=scene_name,
                    acquisition_date=sidecar_datetime_from_text(self.meta.raw_text),
                    pixels=pixels,
                )
            ]
        )

    @property
    def acquisition_audit(self) -> dict:
        """Zarrmony soft-optional hook (zarrmony issue #76): inject
        acquisition-block fields the source Snouty TIFF has no OME surface for.

        Snouty is HT-SOLS (High-Throughput Single-Objective Light-Sheet) by
        construction — every acquisition directory the reader accepts was
        produced by that instrument, so ``imaging_method`` is a static
        contribution rather than a per-scene extraction. ``microscope`` names
        the instrument family; a specific Calico instrument name (``"Snouty"``)
        is stamped by the Aperture ingest form as ``microscope_name``, not
        here (see ADR-0008 § microscope vs microscope_name).

        Fills gaps only — zarrmony uses ``setdefault`` semantics so any key
        the LIF/OME extractors populated wins over this dict.

        The ``zarrmony_snouty`` sub-dict records which engine wrote the
        pixels. It is namespaced rather than flat, because zarrmony's audit
        vocabulary is a documented set of top-level keys and this is a
        reader-specific addition to it.
        """
        return {
            "imaging_method": ["light_sheet"],
            "microscope": "HT-SOLS",
            _engine.AUDIT_KEY: _engine.audit_payload(self.engine, self.engine_fallback_reason),
        }

    def _delayed_volume(self, path: Path):
        m = self.meta
        if self.mode == "raw":
            return dask.delayed(_read_and_crop_plane)(str(path), m.timestamp_strip_px)
        return dask.delayed(_read_crop_and_transform)(
            str(path),
            m.timestamp_strip_px,
            self.mode,
            m.scan_step_size_px,
            m.voxel_aspect_ratio,
            self.transform_footprint_bytes,
            self.engine,
        )


# How far the camera's burned-in stamp is allowed to sit from the ``Date`` and
# ``Time`` the vendor wrote into the matching sidecar. The sidecar is stamped
# when the buffer is flushed to disk, a fraction of a second after the first
# frame in the observed acquisitions. One hour is loose enough that a slow
# flush never trips it, and tight enough to catch a camera whose stamp layout
# differs from the one this reader decodes.
_SIDECAR_STAMP_TOLERANCE = dt.timedelta(hours=1)


def sidecar_datetime_from_text(text: str) -> dt.datetime | None:
    """Combine the ``Date`` and ``Time`` fields of one sidecar into a datetime.

    Returns ``None`` when either field is missing or malformed. Two callers
    rely on that: the timestamp cross-check below treats ``None`` as "no
    cross-check available", and :attr:`ScenePixels.ome_metadata` leaves
    ``acquisition_date`` unset. Neither treats it as a failure.

    Reads the verbatim text rather than :attr:`SnoutyMetadata.raw`, because
    the sidecar parser coerces every value it can and a date is one bad
    coercion away from arithmetic.
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() in ("Date", "Time"):
            fields[key.strip()] = value.strip()
    if "Date" not in fields or "Time" not in fields:
        return None
    try:
        return dt.datetime.strptime(f"{fields['Date']} {fields['Time']}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _sidecar_datetime(path: Path) -> dt.datetime | None:
    """Read the ``Date`` and ``Time`` fields out of one vendor sidecar file.

    Returns ``None`` when the file is absent or unreadable.
    """
    try:
        text = path.read_text()
    except OSError:
        return None
    return sidecar_datetime_from_text(text)


def position_token(path: Path) -> str | None:
    """The scene key of a flat acquisition: the ``MMMMMM`` of ``_pMMMMMM.tif``.

    ``None`` for a file that carries no position, which is the vendor's
    single-position shape.
    """
    match = _POSITION_TIF_RE.match(path.name)
    return match.group("p") if match is not None else None


def _scene_groups(
    ordered: list[Path], group_key: Callable[[Path], str | None]
) -> dict[str | None, list[str]]:
    """Split an ordered file list into per-scene lists of filenames.

    Only the order inside a scene reaches its T axis, so this is the grouping
    that the timestamp/filename agreement check compares.
    """
    groups: dict[str | None, list[str]] = {}
    for path in ordered:
        groups.setdefault(group_key(path), []).append(path.name)
    return groups


def _order_data_files(
    files: list[Path],
    metadata_dir: Path,
    group_key: Callable[[Path], str | None] = position_token,
) -> list[Path]:
    """Order one acquisition's ``.tif`` files along the time axis.

    The primary key is the PCO timestamp that the camera burns into the pixel
    data of frame 0 of every file (see :mod:`zarrmony_snouty._pco_timestamp`).
    The hardware writes it before any software sees the frame, so it is the
    only key that survives a filesystem whose ``st_mtime`` granularity ties
    every file in a run, and a copy made with ``cp -r`` or with ``rsync``
    without ``-t``. Both of those destroy mtime as an ordering key, and both
    used to reorder the T axis silently.

    The fallback is the zero-padded filename order, which the vendor writes as
    ``'%06i_%s.tif' % (t, position_string)``. The reader falls back when:

    - any file carries no stamp this reader recognizes,
    - two files share a camera frame counter,
    - the first file's stamp disagrees with its own sidecar.

    Each fallback emits a :class:`SnoutyTimestampWarning`. Unlike the
    ``st_mtime`` sort it replaces, the fallback is deterministic.

    ``group_key`` names the scene a file belongs to. The flat reader keys on
    the position token, the plate reader on the well-and-field token. Only the
    order inside one scene reaches a T axis, so the agreement check compares
    the groups rather than the whole list.
    """
    by_name = sorted(files, key=lambda p: p.name)
    stamps = {path: _pco_timestamp.read_stamp(path) for path in by_name}

    unreadable = [path.name for path in by_name if stamps[path] is None]
    if unreadable:
        warnings.warn(
            f"{metadata_dir.parent / 'data'}: {len(unreadable)} of {len(by_name)} .tif "
            f"files carry no readable burned-in camera timestamp "
            f"(first: {unreadable[0]}); ordering the time axis by filename instead",
            SnoutyTimestampWarning,
            stacklevel=3,
        )
        return by_name

    counters = [stamps[path].counter for path in by_name]  # type: ignore[union-attr]
    if len(set(counters)) != len(counters):
        warnings.warn(
            f"{metadata_dir.parent / 'data'}: the burned-in camera frame counter repeats "
            f"across .tif files, so it cannot order them; ordering the time axis by "
            f"filename instead",
            SnoutyTimestampWarning,
            stacklevel=3,
        )
        return by_name

    first = by_name[0]
    expected = _sidecar_datetime(metadata_dir / f"{first.stem}.txt")
    stamped = stamps[first].timestamp  # type: ignore[union-attr]
    if expected is not None and abs(stamped - expected) > _SIDECAR_STAMP_TOLERANCE:
        warnings.warn(
            f"{metadata_dir.parent / 'data'}: the burned-in camera timestamp of "
            f"{first.name} reads {stamped.isoformat()} but its sidecar reads "
            f"{expected.isoformat()}; the stamp layout is not the one this reader "
            f"decodes, so it is ordering the time axis by filename instead",
            SnoutyTimestampWarning,
            stacklevel=3,
        )
        return by_name

    by_stamp = sorted(by_name, key=lambda p: stamps[p])  # type: ignore[arg-type,return-value]
    if _scene_groups(by_stamp, group_key) != _scene_groups(by_name, group_key):
        warnings.warn(
            f"{metadata_dir.parent / 'data'}: the burned-in camera timestamps put the "
            f"timepoints of a position in a different order from the filenames; "
            f"trusting the timestamps, because the camera writes them into the pixel "
            f"data at capture time",
            SnoutyTimestampWarning,
            stacklevel=3,
        )
    return by_stamp


def _validate_v01_scope(meta: SnoutyMetadata) -> None:
    if meta.size_t != 1:
        raise SnoutyVolumesPerBufferUnsupportedError(
            f"volumes_per_buffer={meta.size_t} packs multiple volumes into a "
            "single .tif; no real fixture has been staged to verify the "
            "buffer-frame layout, so this shape is not yet implemented."
        )
