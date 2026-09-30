"""Decoder for the PCO binary timestamp burned into every Snouty camera frame.

The Snouty camera runs with its timestamp feature enabled, so the hardware
writes a binary-coded-decimal stamp into the first 14 pixels of row 0 of every
2D frame. The stamp is written before any software touches the data, which
makes it the source of truth for acquisition time. It survives a file copy, a
filesystem with a coarse ``st_mtime``, and an ``rsync`` without ``-t``.

This is the same strip that :data:`~zarrmony_snouty._metadata.TIMESTAMP_STRIP_PX`
crops off before the array reaches callers. The crop stays. This module reads
the stamp first, for ordering only.

Layout
------

Pixel index into row 0, each pixel holding two BCD digits in its low byte
(tens in the high nibble, units in the low nibble):

===========  ==========================================
pixel        field
===========  ==========================================
``0``–``3``  image counter, 8 digits, most significant pixel first
``4``–``5``  year, 4 digits
``6``        month
``7``        day
``8``        hour
``9``        minute
``10``       second
``11``–``13``  microsecond, 6 digits
===========  ==========================================

The image counter is the camera's free-running frame counter. It increments by
one per 2D frame and does not reset between the ``.tif`` files of one
acquisition, so the counter of frame 0 orders the timepoints directly.

Verified against every acquisition on the internal share that this reader
accepts: 115 acquisitions spanning 2023 to 2026, from ten operators, 111
single-position and 4 multi-position. Every sampled frame decoded. Every
decoded time landed within 3.5 seconds of the ``Date`` and ``Time`` fields of
its own vendor sidecar, and the frame-counter order matched the zero-padded
filename order in every case.

Two shapes outside that set are worth naming, because both exercise the
fallback rather than the stamp:

- Hand-made files dropped into ``data/`` by an image-editing tool carry no
  stamp. They decode to ``None`` and the reader sorts by filename.
- Plate tile scans name their files ``NNNNNN_<well>r<row>c<col>.tif`` and the
  stage visits the tiles in a serpentine order, so the frame counter ascends
  in tile order and not in filename order. Those directories do not end in
  ``_ht_sols_snap`` or ``_ht_sols_acquire``, so the reader rejects them before
  this module runs. See the HCS limitation in the README.

Decoding is deliberately fallible. :func:`decode_stamp_row` returns ``None``
rather than raising when the pixels do not hold a plausible stamp, so a camera
that writes a different layout degrades to a filename sort instead of a silent
mis-ordering.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile

__all__ = [
    "PCO_STAMP_PX",
    "PcoStamp",
    "decode_stamp_row",
    "read_stamp",
]

# Number of leading pixels of row 0 that hold the stamp.
PCO_STAMP_PX = 14

# Reject a decoded year outside this range. Random pixel data that happens to
# pass the BCD nibble check still almost never lands inside a plausible
# calendar year, so this is the cheapest guard against a false positive.
_MIN_YEAR = 2000
_MAX_YEAR = 2100


@dataclass(frozen=True, order=True)
class PcoStamp:
    """One frame's burned-in camera timestamp.

    Ordered by ``counter`` first, then ``timestamp``. The counter is the
    stronger key: it is a monotonic integer that cannot step backwards over an
    NTP correction or a daylight-saving change, both of which move the clock.
    """

    counter: int
    timestamp: dt.datetime


def _bcd_byte(pixel: int) -> int | None:
    """Decode one pixel into the two BCD digits in its low byte.

    Returns ``None`` if either nibble is above 9, which is not valid BCD.
    """
    low_byte = int(pixel) & 0xFF
    tens, units = low_byte >> 4, low_byte & 0x0F
    if tens > 9 or units > 9:
        return None
    return tens * 10 + units


def decode_stamp_row(row) -> PcoStamp | None:
    """Decode the PCO stamp from the leading pixels of one frame's row 0.

    Returns ``None`` when ``row`` is too short, when a pixel is not valid BCD,
    or when the digits do not form a plausible calendar date and time. A
    ``None`` result means "this frame carries no stamp I recognize", not
    "this file is broken".
    """
    if len(row) < PCO_STAMP_PX:
        return None
    digits = [_bcd_byte(pixel) for pixel in row[:PCO_STAMP_PX]]
    if any(digit is None for digit in digits):
        return None

    counter = digits[0] * 10**6 + digits[1] * 10**4 + digits[2] * 100 + digits[3]
    year = digits[4] * 100 + digits[5]
    microsecond = digits[11] * 10**4 + digits[12] * 100 + digits[13]
    if not _MIN_YEAR <= year <= _MAX_YEAR:
        return None
    try:
        timestamp = dt.datetime(
            year, digits[6], digits[7], digits[8], digits[9], digits[10], microsecond
        )
    except ValueError:
        # Out-of-range month, day, hour, minute, or second. datetime also
        # rejects second 60, which the camera never writes.
        return None
    return PcoStamp(counter=counter, timestamp=timestamp)


def _stamp_pixels(tif: tifffile.TiffFile, page) -> np.ndarray | None:
    """Return the first :data:`PCO_STAMP_PX` pixels of the page's row 0.

    Snouty writes uncompressed single-strip pages, so the fast path seeks to
    the start of the pixel data and reads 28 bytes. That keeps the cost of
    ordering an acquisition at one header parse per file, which matters on a
    network filesystem where a full page read moves a megabyte.
    """
    if (
        page.is_contiguous
        and not page.is_tiled
        and page.bitspersample == 16
        and page.samplesperpixel == 1
        and page.imagewidth >= PCO_STAMP_PX
    ):
        tif.filehandle.seek(page.dataoffsets[0])
        buffer = tif.filehandle.read(PCO_STAMP_PX * 2)
        if len(buffer) < PCO_STAMP_PX * 2:
            return None
        dtype = np.dtype("<u2" if tif.byteorder == "<" else ">u2")
        return np.frombuffer(buffer, dtype=dtype, count=PCO_STAMP_PX)

    # Compressed or tiled page: decode it and take row 0. No Snouty fixture
    # reaches this path, but a full read is better than refusing to order.
    frame = np.asarray(page.asarray())
    if frame.ndim == 2:
        return frame[0, :PCO_STAMP_PX]
    if frame.ndim == 3:
        return frame[0, :PCO_STAMP_PX, 0]
    return None


def read_stamp(path: Path | str) -> PcoStamp | None:
    """Read the burned-in stamp of the first frame of a Snouty volume TIFF.

    Returns ``None`` when the file cannot be opened, holds no pages, or carries
    no stamp this module recognizes. Every caller must handle ``None`` by
    falling back to another ordering key.
    """
    try:
        with tifffile.TiffFile(path) as tif:
            if not tif.pages:
                return None
            row = _stamp_pixels(tif, tif.pages[0])
    except (OSError, ValueError, IndexError, tifffile.TiffFileError):
        return None
    if row is None:
        return None
    return decode_stamp_row(row)
