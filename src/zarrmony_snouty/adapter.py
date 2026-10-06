"""Reader Protocol adapter for a single Snouty acquisition subdirectory.

Handles single- and multi-position, single- and multi-channel acquisitions
with one or more timepoints. Files under ``data/`` are grouped by position
(the ``MMMMMM`` in ``NNNNNN_pMMMMMM.tif``) into one scene per position;
within each scene, files are concatenated along the T axis (one dask chunk
per timepoint). The T axis follows the timestamp the camera burns into the
pixel data of each file's first frame — see
:func:`zarrmony_snouty._pixels._order_data_files` and
:mod:`zarrmony_snouty._pco_timestamp`. Single-position acquisitions (no
``_pNNNNNN.tif`` files)
expose one scene named after the acquisition directory, preserving v0.1
behavior. Multi-position acquisitions expose scenes named
``<acquisition-dir>__p<zero-padded-index>`` (the double underscore is the
intentional boundary separator so the suffix does not collide with the
vendor's single-underscore filename fragments).

This module owns the grouping and the stage-position attrs. Everything
downstream of the grouping — the read, the crop, the transform, the pixel
sizes — lives in :mod:`zarrmony_snouty._pixels`, which
:class:`zarrmony_snouty.plate.SnoutyPlateReader` shares.

Multi-channel volumes (``channels_per_slice`` with more than one entry) are
laid out on disk as ``(Z, C, Y, X)`` — Z outermost, matching the swap in
``snouty_folder.write_original_ome_tif`` — and surface with the vendor's
channel labels in order along the C axis.

``volumes_per_buffer > 1`` (the vendor's hardware-limited time sampling —
multiple volumes stacked inside one ``.tif``) still raises
``SnoutyVolumesPerBufferUnsupportedError``: the buffer-frame layout inside
a single ``.tif`` has not been verified against a real fixture.

Three output modes are available via the ``mode`` kwarg. The default is
``"desheared"`` as of v0.3; it was ``"raw"`` through v0.2, and the flip is
breaking. Pass ``mode="raw"`` to get the pre-v0.3 output. ``"desheared"`` and
``"traditional"`` port the CPU paths of ``snouty_folder.SnoutyFolder`` (see
Austin Lefebvre's ``snouty-folder`` package at
https://github.com/aelefebv/snouty-folder) — see
:mod:`zarrmony_snouty._deshear`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import xarray as xr

from . import _engine
from ._engine import Engine, ResolvedEngine
from ._errors import (
    SnoutyDataError,
    SnoutyError,
    SnoutyModeError,
    SnoutyVolumesPerBufferUnsupportedError,
)
from ._hostmem import SnoutyHostMemoryError
from ._metadata import SnoutyMetadata, parse_metadata_dir
from ._pixels import (
    _MODES,
    DEFAULT_MODE,
    DTYPE,
    Mode,
    ScenePixels,
    SnoutyTimestampWarning,
    _order_data_files,
    _PixelSizes,
    _validate_v01_scope,
    position_token,
)


class SnoutyXYPositionListError(SnoutyError, ValueError):
    """The parent GUI-session directory's ``XY_stage_position_list.txt`` is
    malformed (unparseable line, wrong arity, or non-numeric values)."""


__all__ = [
    "SnoutyDataError",
    "SnoutyError",
    "SnoutyHostMemoryError",
    "SnoutyModeError",
    "SnoutyReader",
    "SnoutyTimestampWarning",
    "SnoutyVolumesPerBufferUnsupportedError",
    "SnoutyXYPositionListError",
]


_XY_POSITION_LIST_FILENAME = "XY_stage_position_list.txt"

# Sentinel for the SnoutyReader.__init__ ``xy_positions`` kwarg. Distinguishes
# "caller did not supply a list — auto-load from ``../XY_stage_position_list.txt``"
# (default v0.1 behavior) from "caller supplied ``None`` — omit stage attrs even
# if the file exists". SnoutySessionReader uses the explicit override to shut
# off attrs when the session-level list length does not match a child's
# position count.
_XY_POSITIONS_AUTO: object = object()


class SnoutyReader:
    layout_hint = "flat"
    plate_layout = None

    def __init__(
        self,
        path: Path,
        mode: Mode = DEFAULT_MODE,
        *,
        engine: Engine = "auto",
        engine_decision: tuple[ResolvedEngine, str | None] | None = None,
        xy_positions: list[tuple[float, float]] | None | object = _XY_POSITIONS_AUTO,
    ) -> None:
        if mode not in _MODES:
            raise SnoutyModeError(
                f"unknown SnoutyReader mode {mode!r}; expected one of {list(_MODES)}"
            )
        _engine.validate(engine)
        self._dir = Path(path)
        self._mode: Mode = mode
        self._meta: SnoutyMetadata = parse_metadata_dir(self._dir / "metadata")
        # Resolved once, here, before any pixel work. The capacity branch
        # needs the sidecar geometry, so this cannot sit above the parse.
        # ``engine_decision`` is how SnoutySessionReader imposes one engine on
        # every child: the session already resolved, and a child must not
        # second-guess it. Same override precedent as ``xy_positions``.
        self.engine_used, self.engine_fallback_reason = engine_decision or _engine.resolve(
            engine,
            mode,
            lambda: _engine.required_device_bytes(self._meta, itemsize=DTYPE.itemsize),
        )
        self._pixels = ScenePixels(self._meta, mode, self.engine_used, self.engine_fallback_reason)
        found = list((self._dir / "data").glob("*.tif"))
        if not found:
            raise SnoutyDataError(f"no .tif files in {self._dir / 'data'}")
        # Reject an impossible layout before opening N files to read their
        # burned-in timestamps.
        _reject_mixed_positions(found, self._dir / "data")
        all_files = _order_data_files(found, self._dir / "metadata")

        _validate_v01_scope(self._meta)

        # Group by position index; None means a non-``_p`` (legacy single-position) file.
        self._scenes_files: list[tuple[int | None, list[Path]]] = _group_by_position(
            all_files, self._dir / "data"
        )
        if len(self._scenes_files) == 1 and self._scenes_files[0][0] is None:
            self.scenes: list[str] = [self._dir.name]
        else:
            self.scenes = [f"{self._dir.name}__p{i:06d}" for i, _ in self._scenes_files]

        if xy_positions is _XY_POSITIONS_AUTO:
            self._xy_positions = _load_xy_position_list(
                self._dir.parent / _XY_POSITION_LIST_FILENAME
            )
        else:
            self._xy_positions = xy_positions  # type: ignore[assignment]
        self._active = 0

    def set_scene(self, index: int) -> None:
        if not 0 <= index < len(self.scenes):
            raise IndexError(
                f"scene index {index} out of range; valid indices are 0..{len(self.scenes) - 1}"
            )
        self._active = index

    @property
    def xarray_dask_data(self) -> xr.DataArray:
        position_index, files = self._scenes_files[self._active]
        return self._pixels.xarray(files, attrs=self._scene_attrs(position_index))

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

    @property
    def transform_footprint_bytes(self) -> int:
        """Peak host bytes one dask task holds while it transforms a timepoint.

        ``0`` in ``raw`` mode, which runs no transform. See
        :mod:`zarrmony_snouty._hostmem` for the bound this feeds.
        """
        return self._pixels.transform_footprint_bytes

    @property
    def physical_pixel_sizes(self) -> _PixelSizes:
        return self._pixels.physical_pixel_sizes

    @property
    def channel_names(self) -> list[str]:
        return self._pixels.channel_names

    @property
    def dtype(self) -> np.dtype:
        # zarrmony >=0.9 reads reader.dtype in _channels_for_scene to compute
        # the OME-NGFF display window. ``DTYPE`` is the one source of truth,
        # shared with the dask graph below, so this answers without
        # materializing ``xarray_dask_data``.
        return DTYPE

    @property
    def metadata(self) -> str:
        return self._meta.raw_text

    @property
    def acquisition_audit(self) -> dict:
        """Zarrmony soft-optional hook (zarrmony issue #76) — see
        :attr:`zarrmony_snouty._pixels.ScenePixels.acquisition_audit`."""
        return self._pixels.acquisition_audit

    def close(self) -> None:
        pass


def _reject_mixed_positions(files: list[Path], data_dir: Path) -> None:
    """Raise if ``data/`` holds both ``NNNNNN_pMMMMMM.tif`` and plain names.

    Snouty never writes such a mix, and letting it slide would silently drop
    timepoints from one of the scenes. Pure and cheap — a regex per filename,
    no I/O — so the reader runs it before it reads any pixel data.
    """
    shapes = {position_token(f) is not None for f in files}
    if len(shapes) > 1:
        raise SnoutyDataError(
            f"{data_dir} mixes multi-position (_pNNNNNN.tif) and non-position "
            "files; refusing to guess how they map to scenes"
        )


def _group_by_position(files: list[Path], data_dir: Path) -> list[tuple[int | None, list[Path]]]:
    """Group ``.tif`` files by position index encoded in ``NNNNNN_pMMMMMM.tif``.

    Files that do not match the pattern are grouped under ``None`` (legacy
    single-position shape). A mix of the two shapes raises — see
    :func:`_reject_mixed_positions`.
    """
    _reject_mixed_positions(files, data_dir)
    by_position: dict[int | None, list[Path]] = {}
    for f in files:
        token = position_token(f)
        key = int(token) if token is not None else None
        by_position.setdefault(key, []).append(f)

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
