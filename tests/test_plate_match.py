"""Plate-matcher unit tests.

The plate matcher tests contents only. Every fixture here therefore carries a
directory name with no plate token in it, because two of the ten plate
acquisitions on the share carry none either (ADR-0003).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import PlateSpec, write_synthetic_snouty
from zarrmony_snouty.match import match, match_plate, match_session

GRAMMAR_A = PlateSpec(grammar="A", wells=((0, 0), (0, 1)), fields=((0, 0), (0, 1)))
GRAMMAR_B = PlateSpec(grammar="B", wells=((0, 0), (3, 7)))


@pytest.mark.parametrize("plate", [GRAMMAR_A, GRAMMAR_B], ids=["grammar-a", "grammar-b"])
def test_matches_a_plate_whose_name_carries_no_plate_token(tmp_path: Path, plate) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=plate)
    assert "plate" not in fixture.dir.name
    assert "ht_sols_snap" not in fixture.dir.name
    assert match_plate(fixture.dir) == 200


def test_plate_outranks_the_subdir_matcher(tmp_path: Path) -> None:
    """An operator can give a plate a GUI suffix. The plate still wins."""
    fixture = write_synthetic_snouty(
        tmp_path,
        subdir_name="2026-07-14_10-15-35_000_ht_sols_snap",
        plate=GRAMMAR_A,
    )
    assert match(fixture.dir) == 100
    assert match_plate(fixture.dir) == 200


def test_rejects_a_snap_acquisition(synthetic_snouty) -> None:
    assert match_plate(synthetic_snouty.dir) is None
    assert match(synthetic_snouty.dir) == 100


def test_rejects_a_multi_position_acquisition(tmp_path: Path) -> None:
    """``000000_p000001.tif`` is a position, not a well."""
    fixture = write_synthetic_snouty(tmp_path, n_positions=2)
    assert match_plate(fixture.dir) is None
    assert match(fixture.dir) == 100


def test_rejects_a_gui_session(tmp_path: Path) -> None:
    session = tmp_path / "2026-07-14_10-15-30_000_ht_sols_gui"
    session.mkdir()
    write_synthetic_snouty(session)
    assert match_plate(session) is None
    assert match_session(session) == 100


def test_rejects_a_non_existent_path(tmp_path: Path) -> None:
    assert match_plate(tmp_path / "nope") is None


def test_rejects_a_file(tmp_path: Path) -> None:
    path = tmp_path / "not_a_dir.tif"
    path.write_bytes(b"II*\x00")
    assert match_plate(path) is None


def test_rejects_a_directory_without_data(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=GRAMMAR_A)
    for tif in (fixture.dir / "data").iterdir():
        tif.unlink()
    (fixture.dir / "data").rmdir()
    assert match_plate(fixture.dir) is None


def test_rejects_a_directory_without_metadata(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=GRAMMAR_A)
    for sidecar in (fixture.dir / "metadata").iterdir():
        sidecar.unlink()
    (fixture.dir / "metadata").rmdir()
    assert match_plate(fixture.dir) is None


def test_rejects_an_empty_data_directory(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=GRAMMAR_A)
    for tif in (fixture.dir / "data").iterdir():
        tif.unlink()
    assert match_plate(fixture.dir) is None


def test_rejects_a_mix_of_the_two_grammars(tmp_path: Path) -> None:
    """The reader raises on this shape. The matcher must only decline."""
    fixture = write_synthetic_snouty(tmp_path, plate=GRAMMAR_A)
    (fixture.dir / "data" / "000000_r05c05.tif").write_bytes(b"II*\x00")
    assert match_plate(fixture.dir) is None


def test_rejects_a_tif_that_matches_neither_grammar(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=GRAMMAR_A)
    (fixture.dir / "data" / "operator_notes.tif").write_bytes(b"II*\x00")
    assert match_plate(fixture.dir) is None


def test_ignores_non_tif_entries_in_data(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=GRAMMAR_A)
    (fixture.dir / "data" / "notes.txt").write_text("operator notes\n")
    (fixture.dir / "data" / "thumbnails").mkdir()
    assert match_plate(fixture.dir) == 200
