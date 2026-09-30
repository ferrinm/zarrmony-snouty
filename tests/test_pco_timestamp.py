"""Tests for the burned-in PCO camera timestamp decoder (#23).

The golden vector below is the real thing: the first 14 pixels of row 0 of
frame 0 of a Snouty acquisition, copied out of the file. It pins the decoder
to what the hardware writes, so a later refactor cannot quietly redefine the
layout. Fourteen integers carry no sample identity, so they are safe to commit
under the rule in CONTRIBUTING.md.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest
import tifffile

from zarrmony_snouty._pco_timestamp import (
    PCO_STAMP_PX,
    PcoStamp,
    decode_stamp_row,
    read_stamp,
)

from .conftest import encode_pco_stamp

# Measured from a real acquisition: frame counter 2646, captured at
# 2026-07-14 10:53:59.039495. The vendor sidecar for the same file reads
# "Date: 2026-07-14" and "Time: 10:53:59", which is the independent
# confirmation that this layout is the right one.
REAL_STAMP_PIXELS = [0, 0, 38, 70, 32, 38, 7, 20, 16, 83, 89, 3, 148, 149]
REAL_STAMP = PcoStamp(counter=2646, timestamp=dt.datetime(2026, 7, 14, 10, 53, 59, 39495))


def test_decodes_a_real_camera_frame() -> None:
    assert decode_stamp_row(REAL_STAMP_PIXELS) == REAL_STAMP


def test_decodes_a_real_camera_frame_from_a_uint16_row() -> None:
    row = np.array(REAL_STAMP_PIXELS + [1460, 1362, 1398], dtype=np.uint16)
    assert decode_stamp_row(row) == REAL_STAMP


def test_encode_and_decode_round_trip() -> None:
    stamp = PcoStamp(counter=98_765_432, timestamp=dt.datetime(2099, 12, 31, 23, 59, 58, 999999))
    assert decode_stamp_row(encode_pco_stamp(stamp.counter, stamp.timestamp)) == stamp


@pytest.mark.parametrize("pixel", range(PCO_STAMP_PX))
def test_a_non_bcd_nibble_anywhere_rejects_the_row(pixel: int) -> None:
    row = list(REAL_STAMP_PIXELS)
    row[pixel] = 0xAB  # both nibbles above 9
    assert decode_stamp_row(row) is None


def test_a_row_shorter_than_the_stamp_is_rejected() -> None:
    assert decode_stamp_row(REAL_STAMP_PIXELS[:-1]) is None


def test_an_implausible_year_is_rejected() -> None:
    row = list(REAL_STAMP_PIXELS)
    row[4] = 0x19  # year 1926
    assert decode_stamp_row(row) is None


def test_an_impossible_calendar_date_is_rejected() -> None:
    row = list(REAL_STAMP_PIXELS)
    row[6] = 0x13  # month 13
    assert decode_stamp_row(row) is None


def test_ordering_puts_the_frame_counter_first() -> None:
    early = PcoStamp(counter=1, timestamp=dt.datetime(2026, 7, 14, 12, 0, 0))
    # A clock that stepped backwards, on a later frame. The counter wins.
    later = PcoStamp(counter=2, timestamp=dt.datetime(2026, 7, 14, 11, 0, 0))
    assert sorted([later, early]) == [early, later]


def test_read_stamp_reads_the_first_frame_of_a_volume(tmp_path) -> None:
    volume = np.zeros((3, 20, 32), dtype=np.uint16)
    for z in range(3):
        volume[z, 0, :PCO_STAMP_PX] = encode_pco_stamp(
            500 + z, dt.datetime(2026, 7, 14, 10, 0, 0, 1000 * z)
        )
    tifffile.imwrite(tmp_path / "v.tif", volume, photometric="minisblack")

    assert read_stamp(tmp_path / "v.tif") == PcoStamp(
        counter=500, timestamp=dt.datetime(2026, 7, 14, 10, 0, 0)
    )


def test_read_stamp_returns_none_for_a_frame_without_a_stamp(tmp_path) -> None:
    volume = np.full((2, 20, 32), 9999, dtype=np.uint16)
    tifffile.imwrite(tmp_path / "v.tif", volume, photometric="minisblack")
    assert read_stamp(tmp_path / "v.tif") is None


def test_read_stamp_returns_none_for_a_missing_file(tmp_path) -> None:
    assert read_stamp(tmp_path / "absent.tif") is None


def test_read_stamp_returns_none_for_a_file_that_is_not_a_tiff(tmp_path) -> None:
    path = tmp_path / "not-a-tiff.tif"
    path.write_bytes(b"this is not a TIFF")
    assert read_stamp(path) is None


def test_read_stamp_handles_a_compressed_page(tmp_path) -> None:
    """The slow path. Snouty writes uncompressed, so no real file reaches it,
    but a compressed page must still decode rather than fall back."""
    volume = np.zeros((2, 20, 32), dtype=np.uint16)
    volume[:, 0, :PCO_STAMP_PX] = encode_pco_stamp(77, dt.datetime(2026, 1, 2, 3, 4, 5, 60708))
    tifffile.imwrite(tmp_path / "v.tif", volume, photometric="minisblack", compression="zlib")

    assert read_stamp(tmp_path / "v.tif") == PcoStamp(
        counter=77, timestamp=dt.datetime(2026, 1, 2, 3, 4, 5, 60708)
    )
