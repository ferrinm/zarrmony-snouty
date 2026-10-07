"""Synthetic Snouty acquisition-subdirectory fixture.

Writes the minimal on-disk shape the adapter reads: a directory named
``<ts>_000_ht_sols_snap/`` containing one or more 16-bit ``(Z, Y, X)``
volume TIFFs under ``data/`` (written with ``tifffile``) and one
``metadata/*.txt`` per volume mirroring the vendor's key=value sidecar.
Every plane is filled with a distinct value that encodes both its
timepoint and z index so per-plane asserts can check crop boundaries,
Z ordering, and T ordering without relying on all-zeros arrays.

Row 0 of every frame carries a burned-in PCO timestamp, encoded exactly the
way the real camera writes it (verified against real acquisitions in #23).
The reader orders the T axis by that stamp, so a fixture without one would
exercise only the fallback path. ``stamp_rank`` lets a test hand the camera a
different acquisition order from the filename order, and ``burn_timestamps``
turns the stamp off to reach the fallback deliberately.

A plate acquisition is written by passing ``plate=PlateSpec(...)``. The
grammar, the imaged wells, and the fields per well all come from that spec,
and the sidecar and stamp writers are shared with the flat shapes.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pytest
import tifffile

from zarrmony_snouty._metadata import TIMESTAMP_STRIP_PX
from zarrmony_snouty._pco_timestamp import PCO_STAMP_PX

#: Default name for a flat acquisition directory — the vendor GUI writes it.
DEFAULT_SUBDIR_NAME = "2026-07-14_10-15-35_000_ht_sols_snap"

#: Default name for a plate acquisition directory. An operator writes this
#: one by hand, so it carries no GUI suffix and no plate token. The plate
#: matcher must fire on the contents alone (ADR-0003).
DEFAULT_PLATE_SUBDIR_NAME = "2026-07-14_10-15-35_000_scripted_run"


@dataclass(frozen=True)
class PlateSpec:
    """The plate shape to write under ``data/``.

    ``wells`` and ``fields`` hold 0-based ``(row, column)`` pairs in the order
    the fixture writes them, so a test can hand them over out of order and
    then assert that the reader sorts them.

    Grammar A names a well with a row letter and a 1-based column, then names
    the field after it: ``000000_A01r00c00.tif``. Grammar B names the well
    with two 0-based indices and holds one field per well:
    ``000000_r00c00.tif``.
    """

    grammar: Literal["A", "B"] = "A"
    wells: tuple[tuple[int, int], ...] = ((0, 0),)
    fields: tuple[tuple[int, int], ...] = ((0, 0),)

    def __post_init__(self) -> None:
        if self.grammar == "B" and self.fields != ((0, 0),):
            raise ValueError("grammar B holds exactly one field per well")

    def tokens(self) -> tuple[str, ...]:
        """The filename tails, one per field, in write order."""
        if self.grammar == "A":
            return tuple(
                f"{chr(ord('A') + row)}{column + 1:02d}r{field_row:02d}c{field_column:02d}"
                for row, column in self.wells
                for field_row, field_column in self.fields
            )
        return tuple(f"r{row:02d}c{column:02d}" for row, column in self.wells)


@dataclass(frozen=True)
class SnoutyFixture:
    dir: Path
    size_z: int
    size_y: int  # AFTER timestamp-strip crop
    size_x: int
    height_px: int  # BEFORE crop
    channels: tuple[str, ...]
    sample_px_um: float
    scan_step_size_um: float
    voxel_aspect_ratio: float
    scan_step_size_px: float
    n_timepoints: int = 1
    n_positions: int = 1
    plate: PlateSpec | None = None

    def value_for(self, z: int, t: int = 0, p: int = 0, c: int = 0) -> int:
        # Distinct per-plane fill so adapter tests can check the crop boundary,
        # Z / T / position / C ordering without relying on all-zeros arrays.
        # The c=0, p=0 case reduces to 1000 * (t + 1) + z + 1 — same as before
        # channels or positions existed — so single-channel single-position
        # tests stay byte-for-byte identical. 30000 * c keeps the fixture
        # within uint16 for c ∈ {0, 1} at realistic z/t/p sizes.
        return 30000 * c + 10000 * p + 1000 * (t + 1) + z + 1

    def plate_value_for(self, field_index: int, z: int, t: int = 0, c: int = 0) -> int:
        # ``value_for`` strides 10000 per position and overflows uint16 past
        # six of them. A plate fixture routinely has 16 fields, so it gets its
        # own stride: distinct per (field, t, c, z) and inside uint16 for 64
        # fields, 4 timepoints, and 4 channels.
        return 500 * (field_index + 1) + 100 * t + 10 * c + z + 1

    def field_token(self, field_index: int) -> str:
        """The filename tail of one field, in the fixture's write order."""
        assert self.plate is not None, "field_token is for plate fixtures only"
        return self.plate.tokens()[field_index]


def encode_pco_stamp(counter: int, when: dt.datetime) -> np.ndarray:
    """Encode a PCO stamp the way the camera hardware writes it.

    Returns ``PCO_STAMP_PX`` uint16 pixels for row 0: a frame counter of eight
    BCD digits, then the year, month, day, hour, minute, second, and a
    microsecond field of six BCD digits. Each pixel holds two digits in its
    low byte. This is the inverse of
    ``zarrmony_snouty._pco_timestamp.decode_stamp_row``.
    """

    def pack(two_digits: int) -> int:
        return ((two_digits // 10) << 4) | (two_digits % 10)

    digits = [
        counter // 10**6 % 100,
        counter // 10**4 % 100,
        counter // 100 % 100,
        counter % 100,
        when.year // 100,
        when.year % 100,
        when.month,
        when.day,
        when.hour,
        when.minute,
        when.second,
        when.microsecond // 10**4 % 100,
        when.microsecond // 100 % 100,
        when.microsecond % 100,
    ]
    return np.array([pack(d) for d in digits], dtype=np.uint16)


# Wall-clock start of every synthetic acquisition, and the gap the fixture
# leaves between consecutive volumes. The sidecar Date and Time are derived
# from the same clock, so the reader's sidecar cross-check passes.
STAMP_EPOCH = dt.datetime(2026, 7, 14, 10, 15, 35, 125000)
STAMP_VOLUME_GAP = dt.timedelta(seconds=2)
STAMP_FRAME_GAP = dt.timedelta(milliseconds=2)
STAMP_FIRST_COUNTER = 1000

#: The acquisition date of the first volume, as the sidecar records it and as
#: zarrmony serializes it into the audit. The sidecar ``Time`` field has a
#: resolution of one second, so the stamp's microseconds drop.
SIDECAR_ACQUISITION_DATE = STAMP_EPOCH.replace(microsecond=0).isoformat()


def _sidecar_text(fixture: SnoutyFixture, filename: str, when: dt.datetime) -> str:
    lines = [
        f"Date: {when:%Y-%m-%d}",
        f"Time: {when:%H:%M:%S}",
        f"filename: {filename}",
        f"folder_name: {fixture.dir.parent.name}\\{fixture.dir.name}",
        f"channels_per_slice: {fixture.channels!r}",
        f"height_px: {fixture.height_px}",
        f"width_px: {fixture.size_x}",
        f"slices_per_volume: {fixture.size_z}",
        "volumes_per_buffer: 1",
        f"sample_px_um: {fixture.sample_px_um}",
        f"scan_step_size_um: {fixture.scan_step_size_um}",
        f"voxel_aspect_ratio: {fixture.voxel_aspect_ratio}",
        f"scan_step_size_px: {fixture.scan_step_size_px}",
        "tilt_deg: 55.0",
        "autofocus_enabled: False",
        "display: True",
    ]
    return "\n".join(lines) + "\n"


def write_synthetic_snouty(
    root: Path,
    *,
    subdir_name: str | None = None,
    size_z: int = 4,
    size_y_cropped: int = 6,
    # Wide enough to hold the PCO_STAMP_PX-pixel burned-in stamp in row 0.
    size_x: int = 16,
    channels: tuple[str, ...] = ("LED",),
    sample_px_um: float = 0.1755,
    scan_step_size_um: float = 2.14,
    voxel_aspect_ratio: float = 9.997,
    scan_step_size_px: float = 7.0,
    n_timepoints: int = 1,
    n_positions: int = 1,
    plate: PlateSpec | None = None,
    burn_timestamps: bool = True,
    stamp_rank: Callable[[int, int], int] | None = None,
) -> SnoutyFixture:
    """Write a synthetic Snouty subdirectory under ``root``.

    With ``n_timepoints > 1`` writes multiple ``NNNNNN.tif`` + ``NNNNNN.txt``
    pairs (matching the vendor's ``_acquire`` multi-timepoint layout). With
    ``n_positions > 1`` filenames switch to ``NNNNNN_pMMMMMM.tif`` (the
    vendor's multi-position layout); each ``(t, p)`` combination gets a
    distinct per-plane pixel fill so tests can prove position / T ordering
    independently. Returns a ``SnoutyFixture`` with everything a test needs
    to assert against.

    With ``plate=PlateSpec(...)`` filenames instead carry well coordinates,
    one file per field per timepoint, and the directory gets the operator's
    free-text name. Each field gets a distinct per-plane fill through
    ``SnoutyFixture.plate_value_for``. ``plate`` and ``n_positions`` are
    mutually exclusive: a plate addresses its stage locations by well and
    field, never by position ordinal.

    ``stamp_rank(t, p)`` returns the acquisition rank of one volume, which
    fixes both its burned-in stamp and its sidecar ``Date`` and ``Time``. The
    default is the real camera's order: every position of one timepoint, then
    the next timepoint. Pass your own to make the camera clock disagree with
    the filenames. On a plate, ``p`` is the index of the field in the spec's
    write order.

    Set ``burn_timestamps`` to ``False`` to leave row 0 filled with the
    sentinel, which is what a camera with its timestamp feature off writes.
    The reader then falls back to filename order.
    """
    if plate is not None and n_positions != 1:
        raise ValueError("pass either plate= or n_positions=, not both")
    if subdir_name is None:
        subdir_name = DEFAULT_PLATE_SUBDIR_NAME if plate is not None else DEFAULT_SUBDIR_NAME
    subdir = root / subdir_name
    (subdir / "data").mkdir(parents=True)
    (subdir / "metadata").mkdir()
    (subdir / "preview").mkdir()

    height_px = size_y_cropped + TIMESTAMP_STRIP_PX
    fixture = SnoutyFixture(
        dir=subdir,
        size_z=size_z,
        size_y=size_y_cropped,
        size_x=size_x,
        height_px=height_px,
        channels=channels,
        sample_px_um=sample_px_um,
        scan_step_size_um=scan_step_size_um,
        voxel_aspect_ratio=voxel_aspect_ratio,
        scan_step_size_px=scan_step_size_px,
        n_timepoints=n_timepoints,
        n_positions=n_positions,
        plate=plate,
    )

    # One unit per file-per-timepoint: a position on a flat acquisition, a
    # field on a plate.
    tokens = plate.tokens() if plate is not None else None
    n_units = len(tokens) if tokens is not None else n_positions

    def fill_value(z: int, t: int, unit: int, c: int) -> int:
        if plate is not None:
            return fixture.plate_value_for(unit, z, t, c)
        return fixture.value_for(z, t, unit, c)

    n_channels = len(channels)
    frames_per_volume = size_z * n_channels
    if stamp_rank is None:

        def stamp_rank(t: int, p: int) -> int:
            return t * n_units + p

    for t in range(n_timepoints):
        for p in range(n_units):
            # Single-position, single-timepoint fixtures keep the historical
            # ``snap.tif`` name so legacy tests that grep the filename still work.
            if tokens is not None:
                stem = f"{t:06d}_{tokens[p]}"
            elif n_positions > 1:
                stem = f"{t:06d}_p{p:06d}"
            elif n_timepoints == 1:
                stem = "snap"
            else:
                stem = f"{t:06d}"

            rank = stamp_rank(t, p)
            volume_time = STAMP_EPOCH + rank * STAMP_VOLUME_GAP
            first_counter = STAMP_FIRST_COUNTER + rank * frames_per_volume

            if n_channels > 1:
                # Multi-channel Snouty .tif files are laid out (Z, C, Y, X) —
                # Z outermost — matching what tifffile parses as ZCYX and the
                # swap in snouty_folder.write_original_ome_tif.
                volume = np.zeros((size_z, n_channels, height_px, size_x), dtype=np.uint16)
                for z in range(size_z):
                    for c in range(n_channels):
                        volume[z, c, :TIMESTAMP_STRIP_PX, :] = 9999
                        volume[z, c, TIMESTAMP_STRIP_PX:, :] = fill_value(z, t, p, c)
            else:
                volume = np.zeros((size_z, height_px, size_x), dtype=np.uint16)
                for z in range(size_z):
                    # Timestamp strip at the top rows — filled with a sentinel so tests
                    # can confirm it gets cropped and never surfaces to callers.
                    volume[z, :TIMESTAMP_STRIP_PX, :] = 9999
                    volume[z, TIMESTAMP_STRIP_PX:, :] = fill_value(z, t, p, 0)

            if burn_timestamps and size_x >= PCO_STAMP_PX:
                # Row 0 of every 2D frame, in on-disk page order: Z outermost,
                # channel inside it. The camera counts frames, not volumes, so
                # the counter and the clock both advance inside one file.
                flat = volume.reshape(frames_per_volume, height_px, size_x)
                for frame_index in range(frames_per_volume):
                    flat[frame_index, 0, :PCO_STAMP_PX] = encode_pco_stamp(
                        first_counter + frame_index,
                        volume_time + frame_index * STAMP_FRAME_GAP,
                    )

            # photometric="minisblack" silences a future-default deprecation
            # warning in tifffile for small (small_dim, ..., 8) test arrays that
            # its heuristic currently interprets as RGB planes.
            tifffile.imwrite(subdir / "data" / f"{stem}.tif", volume, photometric="minisblack")
            (subdir / "metadata" / f"{stem}.txt").write_text(
                _sidecar_text(fixture, f"{stem}.tif", volume_time)
            )

    return fixture


def assert_no_metadata_warnings(audit: dict) -> None:
    """Fail if any store zarrmony wrote recorded a metadata extraction failure.

    ``metadata_warnings`` is the record zarrmony writes into the store when a
    soft-optional reader surface raises. It is the durable half of the
    ``ExtractorWarning`` the console shows, so a test that only silences the
    console still ships the failure (#39).

    Accepts either audit shape, keyed on the layout zarrmony reports: a
    ``per-scene`` convert returns one record per output store under
    ``stores``, and a ``plate`` convert returns the one store record itself.
    """
    stores = audit["stores"] if audit["layout"] == "per-scene" else [audit]
    for store in stores:
        assert store["metadata_warnings"] == [], store["metadata_warnings"]


@pytest.fixture
def synthetic_snouty(tmp_path: Path) -> SnoutyFixture:
    return write_synthetic_snouty(tmp_path)
