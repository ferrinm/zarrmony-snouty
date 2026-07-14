"""Matcher unit tests. The matcher must be cheap and side-effect-free."""

from __future__ import annotations

from pathlib import Path

import pytest

from zarrmony_snouty.match import match


def test_matches_snap_subdir(synthetic_snouty) -> None:
    assert match(synthetic_snouty.dir) == 100


@pytest.mark.parametrize("suffix", ["_ht_sols_snap", "_ht_sols_acquire"])
def test_matches_both_gui_verbs(tmp_path: Path, suffix: str) -> None:
    from tests.conftest import write_synthetic_snouty

    fixture = write_synthetic_snouty(tmp_path, subdir_name=f"2026-07-14_10-15-35_000{suffix}")
    assert match(fixture.dir) == 100


def test_rejects_non_existent_path(tmp_path: Path) -> None:
    assert match(tmp_path / "nope") is None


def test_rejects_file_at_path(tmp_path: Path) -> None:
    p = tmp_path / "not_a_dir.txt"
    p.write_text("hello")
    assert match(p) is None


def test_rejects_dir_without_ht_sols_suffix(tmp_path: Path) -> None:
    d = tmp_path / "some_random_dir"
    (d / "data").mkdir(parents=True)
    (d / "metadata").mkdir()
    (d / "data" / "x.tif").write_bytes(b"II*\x00")
    (d / "metadata" / "x.txt").write_text("k: v\n")
    assert match(d) is None


def test_rejects_dir_missing_data_subdir(tmp_path: Path) -> None:
    d = tmp_path / "2026-07-14_10-15-35_000_ht_sols_snap"
    (d / "metadata").mkdir(parents=True)
    (d / "metadata" / "x.txt").write_text("k: v\n")
    assert match(d) is None


def test_rejects_dir_missing_metadata_subdir(tmp_path: Path) -> None:
    d = tmp_path / "2026-07-14_10-15-35_000_ht_sols_snap"
    (d / "data").mkdir(parents=True)
    (d / "data" / "x.tif").write_bytes(b"II*\x00")
    assert match(d) is None


def test_rejects_empty_data_dir(tmp_path: Path) -> None:
    d = tmp_path / "2026-07-14_10-15-35_000_ht_sols_snap"
    (d / "data").mkdir(parents=True)
    (d / "metadata").mkdir()
    (d / "metadata" / "x.txt").write_text("k: v\n")
    assert match(d) is None


def test_rejects_metadata_without_txt(tmp_path: Path) -> None:
    d = tmp_path / "2026-07-14_10-15-35_000_ht_sols_snap"
    (d / "data").mkdir(parents=True)
    (d / "metadata").mkdir()
    (d / "data" / "x.tif").write_bytes(b"II*\x00")
    (d / "metadata" / "x.json").write_text("{}")
    assert match(d) is None
