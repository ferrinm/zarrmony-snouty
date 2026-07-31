"""Session-matcher unit tests. The session matcher must be cheap and not
require ``XY_stage_position_list.txt`` — some GUI sessions never move the
stage and never write the file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import write_synthetic_snouty
from zarrmony_snouty.match import match, match_session


def _make_session(root: Path, *subdir_names: str) -> Path:
    session = root / "2026-07-14_10-12-21_ht_sols_gui"
    session.mkdir()
    for name in subdir_names:
        write_synthetic_snouty(session, subdir_name=name)
    return session


def test_matches_session_with_snap_child(tmp_path: Path) -> None:
    session = _make_session(tmp_path, "2026-07-14_10-15-35_000_ht_sols_snap")
    assert match_session(session) == 100


def test_matches_session_with_acquire_child(tmp_path: Path) -> None:
    session = _make_session(tmp_path, "2026-07-14_10-16-14_000_ht_sols_acquire")
    assert match_session(session) == 100


def test_matches_session_with_mixed_children(tmp_path: Path) -> None:
    session = _make_session(
        tmp_path,
        "2026-07-14_10-15-35_000_ht_sols_snap",
        "2026-07-14_10-16-14_000_ht_sols_acquire",
    )
    assert match_session(session) == 100


def test_does_not_require_xy_position_list(tmp_path: Path) -> None:
    session = _make_session(tmp_path, "2026-07-14_10-15-35_000_ht_sols_snap")
    assert not (session / "XY_stage_position_list.txt").exists()
    assert match_session(session) == 100


def test_rejects_session_without_children(tmp_path: Path) -> None:
    session = tmp_path / "2026-07-14_10-12-21_ht_sols_gui"
    session.mkdir()
    (session / "XY_stage_position_list.txt").write_text("[0.0, 0.0],\n")
    assert match_session(session) is None


def test_rejects_dir_without_gui_suffix(tmp_path: Path) -> None:
    session = tmp_path / "some_random_dir"
    session.mkdir()
    write_synthetic_snouty(session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap")
    assert match_session(session) is None


def test_rejects_non_existent_path(tmp_path: Path) -> None:
    assert match_session(tmp_path / "nope") is None


def test_rejects_file_at_path(tmp_path: Path) -> None:
    p = tmp_path / "not_a_dir.txt"
    p.write_text("hello")
    assert match_session(p) is None


def test_session_matcher_does_not_fire_on_individual_subdir(tmp_path: Path) -> None:
    # The subdir matcher owns individual _ht_sols_snap/_acquire dirs; the
    # session matcher must not overreach and claim them too, otherwise
    # zarrmony would ambiguously match both plugins on the same input.
    fixture = write_synthetic_snouty(tmp_path)
    assert match_session(fixture.dir) is None


def test_subdir_matcher_does_not_fire_on_session_dir(tmp_path: Path) -> None:
    # Symmetric guardrail: the subdir matcher must reject the parent GUI
    # session dir (which never has data/ + metadata/ children of its own).
    session = _make_session(tmp_path, "2026-07-14_10-15-35_000_ht_sols_snap")
    assert match(session) is None


@pytest.mark.parametrize("suffix", ["_ht_sols_gui"])
def test_matcher_ignores_top_level_position_files(tmp_path: Path, suffix: str) -> None:
    # Session-level XY / focus position lists live at the session root
    # alongside subdirs — the matcher must not confuse them with children.
    session = _make_session(tmp_path, f"2026-07-14_10-15-35_000{suffix.replace('_gui', '_snap')}")
    (session / "XY_stage_position_list.txt").write_text("[0.0, 0.0],\n")
    (session / "focus_piezo_position_list.txt").write_text("[0.0],\n")
    assert match_session(session) == 100
