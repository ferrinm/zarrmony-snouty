"""Reader Protocol adapter for a Snouty multiwell-plate acquisition.

A plate acquisition is one directory with sibling ``data/`` and ``metadata/``
directories, written by an operator-edited python script rather than by the
vendor GUI. The whole plate map lives in the ``data/`` filenames, in one of
two grammars:

- **Grammar A**, from the vendor ``get_multiwell_plate_positions`` generator.
  ``000000_A01r00c00.tif`` is timepoint 0, well ``A01``, field ``r00c00``.
- **Grammar B**, from a hand-rolled operator loop. ``000000_r00c00.tif`` is
  timepoint 0 and well row 0, column 0, with one field per well.

One scene per field. Each scene is ``(T, C, Z, Y, X)``, exactly as the flat
reader builds it — the two share :class:`zarrmony_snouty._pixels.ScenePixels`,
so a plate field and a flat position go through one pixel path. The reader
exposes ``layout_hint = "plate"`` and a ``plate_layout``, so
``zarrmony convert`` writes one OME-NGFF HCS plate store.

Neither grammar records the size of the physical plate, and both record
absolute well coordinates, so the observed well extent is a lower bound and
never the plate itself. The reader snaps that extent up to the smallest
standard format that contains it. The embedded acquisition script is never
read: on one real acquisition the script and the data disagree about which
wells were imaged. ADR-0003 records both decisions and the evidence for them.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import xarray as xr
from zarrmony.readers.plate import Acquisition, PlateField, PlateLayout

from . import _engine
from ._engine import Engine
from ._errors import SnoutyDataError, SnoutyError, SnoutyModeError
from ._metadata import SnoutyMetadata, parse_metadata_dir, parse_metadata_file
from ._pixels import (
    _MODES,
    DEFAULT_MODE,
    DTYPE,
    Mode,
    ScenePixels,
    _order_data_files,
    _PixelSizes,
    _validate_v01_scope,
)
from .match import PLATE_A_RE, PLATE_B_RE, PlateGrammar

__all__ = [
    "STANDARD_PLATE_FORMATS",
    "SnoutyPlateFormatError",
    "SnoutyPlateFormatWarning",
    "SnoutyPlateReader",
]

#: The standard plate formats, as ``well count -> (rows, columns)``. The
#: reader snaps the observed well extent up to the smallest entry that
#: contains it. Ordered smallest first, because the lookup takes the first
#: entry that fits.
STANDARD_PLATE_FORMATS: dict[int, tuple[int, int]] = {
    6: (2, 3),
    12: (3, 4),
    24: (4, 6),
    48: (6, 8),
    96: (8, 12),
    384: (16, 24),
    1536: (32, 48),
}


class SnoutyPlateFormatError(SnoutyError, ValueError):
    """The ``plate_format`` kwarg names no standard format, or it names a
    format too small to hold the wells the filenames report."""


class SnoutyPlateFormatWarning(UserWarning):
    """No standard plate format contains the observed wells, so the reader
    fell back to the observed bounding box.

    The output then declares a plate that no vendor sells. Pass
    ``plate_format=<well count>`` to name the real one.
    """


@dataclass(frozen=True)
class _PlateFile:
    """One ``data/*.tif``, with its well and field coordinates decoded."""

    path: Path
    token: str  # the filename tail that names the field: "A01r00c00" or "r00c00"
    row_index: int  # 0-based well row
    column_index: int  # 0-based well column
    field_row: int  # 0-based field row inside the well
    field_column: int  # 0-based field column inside the well
    field_name: str | None  # "r00c00" under grammar A, None under grammar B

    @property
    def sort_key(self) -> tuple[int, int, int, int]:
        """Wells ascending, then fields inside a well ascending.

        Both grammars snake across the plate, and the snake carries no
        meaning, so acquisition order is not preserved.
        """
        return (self.row_index, self.column_index, self.field_row, self.field_column)


def _plate_token(path: Path) -> str:
    """The part of a plate filename that names the field: everything between
    the timepoint and the extension.

    Every file reaching this function has already matched one of the two
    grammars, so the leading ``NNNNNN_`` is there to split on.
    """
    return path.stem.split("_", 1)[1]


def _parse_one(path: Path) -> tuple[PlateGrammar, _PlateFile] | None:
    """Decode one filename, or return ``None`` if it matches neither grammar."""
    match = PLATE_A_RE.match(path.name)
    if match is not None:
        return "A", _PlateFile(
            path=path,
            token=_plate_token(path),
            row_index=ord(match["row"]) - ord("A"),
            column_index=int(match["col"]) - 1,
            field_row=int(match["frow"]),
            field_column=int(match["fcol"]),
            field_name=f"r{match['frow']}c{match['fcol']}",
        )
    match = PLATE_B_RE.match(path.name)
    if match is not None:
        return "B", _PlateFile(
            path=path,
            token=_plate_token(path),
            row_index=int(match["row"]),
            column_index=int(match["col"]),
            field_row=0,
            field_column=0,
            # Under grammar B, r00c00 is the well. The well has one field and
            # that field has no vendor label of its own.
            field_name=None,
        )
    return None


def _parse_plate_files(files: list[Path], data_dir: Path) -> tuple[PlateGrammar, list[_PlateFile]]:
    """Decode every ``.tif`` in one plate ``data/`` directory.

    Strict, like ``adapter._reject_mixed_positions``. A file that matches
    neither grammar raises, and so does a directory that mixes the two. There
    is no skip-with-warning and no fallback to the flat reader: both grammars
    write ``r<NN>c<NN>``, so a wrong guess silently maps 384 wells onto one.
    """
    if not files:
        raise SnoutyDataError(f"no .tif files in {data_dir}")

    grammar: PlateGrammar | None = None
    first_path: Path | None = None
    records: list[_PlateFile] = []
    for path in sorted(files, key=lambda p: p.name):
        decoded = _parse_one(path)
        if decoded is None:
            raise SnoutyDataError(
                f"{path} matches neither plate filename grammar; expected "
                f"NNNNNN_<Row><CC>r<NN>c<NN>.tif or NNNNNN_r<NN>c<NN>.tif in {data_dir}"
            )
        found, record = decoded
        if grammar is None:
            grammar, first_path = found, path
        elif found != grammar:
            raise SnoutyDataError(
                f"{data_dir} mixes the two plate filename grammars: {first_path.name} "
                f"is grammar {grammar} and {path.name} is grammar {found}. The two "
                "write the same r<NN>c<NN> token for different things, so refusing "
                "to guess which wells these files name"
            )
        if found == "A" and record.column_index < 0:
            raise SnoutyDataError(
                f"{path} names well column 00, but grammar A columns are 1-based; "
                f"refusing to guess which column it means in {data_dir}"
            )
        records.append(record)
    assert grammar is not None  # the loop runs at least once, per the guard above
    return grammar, records


def _row_label(index: int) -> str:
    """``0`` to ``"A"``, ``25`` to ``"Z"``, ``26`` to ``"AA"``.

    Plates past 26 rows exist — a 1536-well plate has 32 — and OME-NGFF rows
    are names, not numbers.
    """
    label = ""
    remaining = index + 1
    while remaining > 0:
        remaining, remainder = divmod(remaining - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def _column_label(index: int) -> str:
    """``0`` to ``"01"``. 1-based and zero-padded to two digits, because
    ``writers/plate.py`` writes the well group path as ``f"{row}/{column}"``
    and this string lands on disk verbatim."""
    return f"{index + 1:02d}"


def _infer_grid(
    observed_rows: int, observed_columns: int, plate_format: int | None, data_dir: Path
) -> tuple[int, int]:
    """Decide the full plate grid from the wells the filenames report.

    Both grammars record absolute well coordinates, so the observed maximum
    is a lower bound on the plate and never the plate itself. A 384-well
    plate imaged in 20 wells of two columns observes a 12 by 2 extent.
    """
    if plate_format is not None:
        if plate_format not in STANDARD_PLATE_FORMATS:
            raise SnoutyPlateFormatError(
                f"plate_format={plate_format} is not a standard plate format; "
                f"expected one of {sorted(STANDARD_PLATE_FORMATS)}"
            )
        rows, columns = STANDARD_PLATE_FORMATS[plate_format]
        if rows < observed_rows or columns < observed_columns:
            raise SnoutyPlateFormatError(
                f"plate_format={plate_format} is a {rows}x{columns} plate, but "
                f"{data_dir} names wells across {observed_rows} rows and "
                f"{observed_columns} columns"
            )
        return rows, columns

    for rows, columns in STANDARD_PLATE_FORMATS.values():
        if rows >= observed_rows and columns >= observed_columns:
            return rows, columns

    warnings.warn(
        f"{data_dir} names wells across {observed_rows} rows and "
        f"{observed_columns} columns, which no standard plate format contains; "
        f"reporting the observed {observed_rows}x{observed_columns} bounding box "
        f"as the plate. Pass plate_format=<well count> to name the real plate.",
        SnoutyPlateFormatWarning,
        stacklevel=3,
    )
    return observed_rows, observed_columns


class SnoutyPlateReader:
    """Reader-protocol adapter for a Snouty multiwell-plate acquisition.

    One scene per imaged field, sorted by well and then by field inside the
    well. ``plate_layout`` carries the full inferred grid, so unimaged wells
    keep their row and column names even though they get no well group.
    """

    layout_hint = "plate"

    def __init__(
        self,
        path: Path,
        mode: Mode = DEFAULT_MODE,
        *,
        engine: Engine = "auto",
        plate_format: int | None = None,
    ) -> None:
        if mode not in _MODES:
            raise SnoutyModeError(
                f"unknown SnoutyPlateReader mode {mode!r}; expected one of {list(_MODES)}"
            )
        _engine.validate(engine)
        self._dir = Path(path)
        self._mode: Mode = mode

        data_dir = self._dir / "data"
        found = list(data_dir.glob("*.tif"))
        if not found:
            raise SnoutyDataError(f"no .tif files in {data_dir}")
        # Reject an impossible layout before reading any sidecar or any pixel.
        self.grammar, records = _parse_plate_files(found, data_dir)

        self._meta: SnoutyMetadata = self._parse_sidecar(records[0].path)
        # One engine for the whole plate. Every field shares one sidecar
        # geometry, so one decision sizes them all — same rule the session
        # reader applies under ``auto`` (issue #10).
        self.engine_used, self.engine_fallback_reason = _engine.resolve(
            engine,
            mode,
            lambda: _engine.required_device_bytes(self._meta, itemsize=DTYPE.itemsize),
        )
        self._pixels = ScenePixels(self._meta, mode, self.engine_used, self.engine_fallback_reason)

        _validate_v01_scope(self._meta)

        # The leading %06i is the timepoint, so grouping by field token and
        # then ordering inside the group by the burned-in stamp is the same
        # rule the flat reader follows (#23). ``records`` is already in
        # filename order.
        field_of: dict[str, _PlateFile] = {}
        files_of: dict[str, list[Path]] = {}
        for record in records:
            field_of.setdefault(record.token, record)
            files_of.setdefault(record.token, []).append(record.path)

        # Order the time axis only when there is one to order. Reading the
        # burned-in stamp costs one file open each, and a full 384-well plate
        # holds 3456 files on a network share, where one open measured at
        # several seconds. Every plate acquisition observed has one timepoint
        # per field, and with one file per field any order is the same order.
        if max(len(group) for group in files_of.values()) > 1:
            files_of = {}
            for file_path in _order_data_files(found, self._dir / "metadata", _plate_token):
                files_of.setdefault(_plate_token(file_path), []).append(file_path)

        self._fields = sorted(field_of.values(), key=lambda record: record.sort_key)
        self._scenes_files: list[list[Path]] = [files_of[f.token] for f in self._fields]
        self.scenes: list[str] = [self._scene_name(f) for f in self._fields]
        self.plate_layout = self._build_plate_layout(plate_format, data_dir)
        self._active = 0

    def _parse_sidecar(self, first_data_file: Path) -> SnoutyMetadata:
        """Read the geometry from the sidecar of the first data file.

        ``parse_metadata_dir`` picks the oldest ``.txt``, which costs one
        ``stat()`` per sidecar. A full 384-well plate has 3456 of them, and
        3456 stats measured at 246 s on the share. The vendor writes one
        sidecar per ``.tif`` with one fixed geometry per run, so naming the
        file directly answers the same question with one open.

        A plate whose first sidecar is missing falls back to the directory
        scan, so a partial copy still reads.
        """
        sidecar = self._dir / "metadata" / f"{first_data_file.stem}.txt"
        if sidecar.is_file():
            return parse_metadata_file(sidecar)
        return parse_metadata_dir(self._dir / "metadata")

    def _scene_name(self, record: _PlateFile) -> str:
        """``<acquisition-dir>__<canonical-well><field-token>``, always suffixed.

        Grammar A gives ``<dir>__A01r00c00``. Grammar B gives ``<dir>__A01``,
        with the canonical well label rather than the raw ``r00c00`` token.
        The double underscore is the boundary separator the flat reader uses,
        so the suffix cannot collide with a vendor filename fragment.
        """
        well = _row_label(record.row_index) + _column_label(record.column_index)
        return f"{self._dir.name}__{well}{record.field_name or ''}"

    def _build_plate_layout(self, plate_format: int | None, data_dir: Path) -> PlateLayout:
        n_rows, n_columns = _infer_grid(
            max(f.row_index for f in self._fields) + 1,
            max(f.column_index for f in self._fields) + 1,
            plate_format,
            data_dir,
        )
        return PlateLayout(
            name=self._dir.name,
            # The full grid, not the imaged subset: zarrmony requires every
            # physical row and column here, and reports the unimaged ones
            # through their absence from the well groups instead.
            rows=[_row_label(i) for i in range(n_rows)],
            columns=[_column_label(i) for i in range(n_columns)],
            # One pass over the plate. ``maximumfieldcount`` stays unset
            # because the writer counts the fields itself.
            acquisitions=[Acquisition(id=0, name=self._dir.name)],
            fields=[
                PlateField(
                    scene_index=index,
                    row=_row_label(record.row_index),
                    column=_column_label(record.column_index),
                    field_name=record.field_name,
                    acquisition_id=None,
                )
                for index, record in enumerate(self._fields)
            ],
        )

    def set_scene(self, index: int) -> None:
        if not 0 <= index < len(self.scenes):
            raise IndexError(
                f"scene index {index} out of range; valid indices are 0..{len(self.scenes) - 1}"
            )
        self._active = index

    @property
    def xarray_dask_data(self) -> xr.DataArray:
        return self._pixels.xarray(self._scenes_files[self._active])

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
