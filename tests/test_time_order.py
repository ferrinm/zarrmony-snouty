"""Tests for how the reader orders the time axis (#23).

The reader used to sort ``data/*.tif`` by ``st_mtime`` alone. On a filesystem
whose mtime granularity ties every file in one acquisition, ``sorted`` is
stable, so the order fell back to ``os.scandir`` order. That order is
arbitrary, and the T axis of the converted store was silently wrong.

Every test here ties the mtimes with ``os.utime`` first, so the failure
reproduces on any host instead of only on the cluster. Tying the mtimes is
enough, because ``os.scandir`` order is arbitrary everywhere: on APFS, six
files created as ``000000`` to ``000005`` in ascending order list as
``000000, 000001, 000003, 000002, 000005, 000004``.

The reader now orders the T axis by the timestamp the camera burns into the
pixel data of each file's first frame, so no filesystem metadata decides it.
"""

from __future__ import annotations

import datetime as dt
import os
import warnings
from pathlib import Path

import pytest

from tests.conftest import STAMP_EPOCH, write_synthetic_snouty
from zarrmony_snouty.adapter import (
    SnoutyReader,
    SnoutyTimestampWarning,
    _order_data_files,
)
from zarrmony_snouty.session import SnoutySessionReader

TIED_MTIME = 1_000_000_000.0


def tie_all_mtimes(root: Path, when: float = TIED_MTIME) -> None:
    """Give every file under ``root`` one identical mtime.

    This is what a filesystem with a coarse timestamp granularity reports for
    a whole acquisition. A copy made with ``cp -r``, or with ``rsync`` without
    ``-t``, has the same effect on a filesystem with fine timestamps.
    """
    for path in sorted(root.rglob("*")):
        if path.is_file():
            os.utime(path, (when, when))


def first_row_of_each_timepoint(reader: SnoutyReader) -> list[int]:
    """One distinguishing pixel per timepoint, in T order."""
    computed = reader.xarray_dask_data.data.compute()
    return [int(computed[t, 0, 0, 0, 0]) for t in range(computed.shape[0])]


# --------------------------------------------------------------------------
# The bug itself
# --------------------------------------------------------------------------


def test_identical_mtimes_do_not_disturb_the_time_axis(tmp_path: Path) -> None:
    """The issue's acceptance criterion. Every file shares one mtime, so mtime
    carries no ordering information at all, and the T axis must still be
    right."""
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=4)
    tie_all_mtimes(fixture.dir)

    mtimes = {p.stat().st_mtime for p in (fixture.dir / "data").glob("*.tif")}
    assert len(mtimes) == 1, "the fixture must tie every mtime for this test to mean anything"

    reader = SnoutyReader(fixture.dir)
    assert first_row_of_each_timepoint(reader) == [fixture.value_for(0, t) for t in range(4)]


def test_ordering_ignores_the_order_the_filesystem_lists_files_in(tmp_path: Path) -> None:
    """The reader reads ``data/`` with ``glob``, which returns ``os.scandir``
    order. That order is arbitrary. Hand the ordering function a deliberately
    wrong input order with tied mtimes: a stable sort on mtime alone would
    hand it straight back."""
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=4)
    tie_all_mtimes(fixture.dir)

    scrambled = sorted((fixture.dir / "data").glob("*.tif"), reverse=True)
    ordered = _order_data_files(scrambled, fixture.dir / "metadata")

    assert [p.name for p in scrambled] != [p.name for p in ordered], (
        "the input order must differ from the correct order, or this test proves nothing"
    )
    assert [p.name for p in ordered] == [f"{t:06d}.tif" for t in range(4)]


def test_reader_survives_an_arbitrary_directory_listing_order(tmp_path, monkeypatch) -> None:
    """The end-to-end form of the bug, through the public reader.

    ``glob`` returns ``os.scandir`` order. Measured on APFS, six files created
    as ``000000`` to ``000005`` in ascending order list as ``000000, 000001,
    000003, 000002, 000005, 000004``, so the order is neither creation order
    nor name order on any host. This test forces one specific wrong order, so
    the coverage does not depend on which order the host happens to produce.
    """
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=4)
    tie_all_mtimes(fixture.dir)

    real_glob = Path.glob

    def reversed_glob(self, pattern, **kwargs):
        return iter(sorted(real_glob(self, pattern, **kwargs), reverse=True))

    monkeypatch.setattr(Path, "glob", reversed_glob)

    reader = SnoutyReader(fixture.dir)
    assert first_row_of_each_timepoint(reader) == [fixture.value_for(0, t) for t in range(4)]


def test_multi_position_time_axis_survives_identical_mtimes(tmp_path: Path) -> None:
    """Each position carries its own T axis. A tied mtime must not shuffle
    either of them, and must not leak a timepoint across positions."""
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=3, n_positions=2)
    tie_all_mtimes(fixture.dir)

    reader = SnoutyReader(fixture.dir)
    for p in range(2):
        reader.set_scene(p)
        assert first_row_of_each_timepoint(reader) == [fixture.value_for(0, t, p) for t in range(3)]


def test_session_child_time_axis_survives_identical_mtimes(tmp_path: Path) -> None:
    session_dir = tmp_path / "2026-07-14_10-12-21_ht_sols_gui"
    session_dir.mkdir()
    fixture = write_synthetic_snouty(
        session_dir,
        subdir_name="2026-07-14_10-21-31_000_ht_sols_acquire",
        n_timepoints=4,
    )
    tie_all_mtimes(session_dir)

    reader = SnoutySessionReader(session_dir)
    reader.set_scene(0)
    computed = reader.xarray_dask_data.data.compute()
    assert [int(computed[t, 0, 0, 0, 0]) for t in range(4)] == [
        fixture.value_for(0, t) for t in range(4)
    ]


def test_sidecar_pick_survives_identical_mtimes(tmp_path: Path) -> None:
    """The third site. ``parse_metadata_dir`` picks the oldest sidecar, and a
    tied mtime used to make that pick arbitrary. The geometry of the whole
    scene comes from the file it picks."""
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=3)
    # Give the later sidecars a geometry that would be visibly wrong if picked.
    for t in (1, 2):
        path = fixture.dir / "metadata" / f"{t:06d}.txt"
        path.write_text(path.read_text().replace("slices_per_volume: 4", "slices_per_volume: 99"))
    tie_all_mtimes(fixture.dir)

    reader = SnoutyReader(fixture.dir)
    assert reader.xarray_dask_data.shape[2] == fixture.size_z


# --------------------------------------------------------------------------
# The burned-in stamp is the source of truth
# --------------------------------------------------------------------------


def test_the_burned_in_stamp_beats_the_filename_when_they_disagree(tmp_path: Path) -> None:
    """The camera writes the stamp into the pixel data at capture time, so it
    outranks a filename that some later tool can rewrite. Here the camera
    captured the volumes in the reverse of their filename order."""
    n = 3
    fixture = write_synthetic_snouty(
        tmp_path,
        n_timepoints=n,
        stamp_rank=lambda t, p: (n - 1) - t,
    )
    tie_all_mtimes(fixture.dir)

    with pytest.warns(SnoutyTimestampWarning, match="different order from the filenames"):
        reader = SnoutyReader(fixture.dir)

    # T=0 must hold the volume the camera captured first, which is 000002.tif.
    assert first_row_of_each_timepoint(reader) == [
        fixture.value_for(0, t) for t in reversed(range(n))
    ]


def test_agreeing_stamps_and_filenames_warn_about_nothing(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=4)
    with warnings.catch_warnings():
        warnings.simplefilter("error", SnoutyTimestampWarning)
        SnoutyReader(fixture.dir)


# --------------------------------------------------------------------------
# Falling back, deterministically
# --------------------------------------------------------------------------


def test_falls_back_to_filename_order_when_no_frame_carries_a_stamp(tmp_path: Path) -> None:
    """A camera with its timestamp feature switched off. The fallback is the
    zero-padded filename, which is still deterministic, unlike mtime."""
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=3, burn_timestamps=False)
    tie_all_mtimes(fixture.dir)

    with pytest.warns(SnoutyTimestampWarning, match="no readable burned-in camera timestamp"):
        reader = SnoutyReader(fixture.dir)

    assert first_row_of_each_timepoint(reader) == [fixture.value_for(0, t) for t in range(3)]


def test_falls_back_when_two_files_share_a_frame_counter(tmp_path: Path) -> None:
    """Every volume claims the same camera frame, so the stamp cannot order
    anything. Refuse to trust it rather than pick arbitrarily."""
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=3, stamp_rank=lambda t, p: 0)
    tie_all_mtimes(fixture.dir)

    with pytest.warns(SnoutyTimestampWarning, match="frame counter repeats"):
        reader = SnoutyReader(fixture.dir)

    assert first_row_of_each_timepoint(reader) == [fixture.value_for(0, t) for t in range(3)]


def test_falls_back_when_the_stamp_disagrees_with_its_sidecar(tmp_path: Path) -> None:
    """The cross-check that guards against a camera whose stamp layout is not
    the one this reader decodes. A layout error shows up as a decoded time
    nowhere near the time the vendor recorded."""
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=3)
    sidecar = fixture.dir / "metadata" / "000000.txt"
    wrong_day = STAMP_EPOCH + dt.timedelta(days=5)
    sidecar.write_text(
        sidecar.read_text().replace(f"Date: {STAMP_EPOCH:%Y-%m-%d}", f"Date: {wrong_day:%Y-%m-%d}")
    )
    tie_all_mtimes(fixture.dir)

    with pytest.warns(SnoutyTimestampWarning, match="stamp layout is not the one this reader"):
        reader = SnoutyReader(fixture.dir)

    assert first_row_of_each_timepoint(reader) == [fixture.value_for(0, t) for t in range(3)]


def test_a_missing_sidecar_does_not_block_the_stamp(tmp_path: Path) -> None:
    """The cross-check is evidence when it is available, not a requirement.
    Some runs write one sidecar for the whole acquisition."""
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=3)
    (fixture.dir / "metadata" / "000000.txt").unlink()
    tie_all_mtimes(fixture.dir)

    with warnings.catch_warnings():
        warnings.simplefilter("error", SnoutyTimestampWarning)
        reader = SnoutyReader(fixture.dir)

    assert first_row_of_each_timepoint(reader) == [fixture.value_for(0, t) for t in range(3)]
