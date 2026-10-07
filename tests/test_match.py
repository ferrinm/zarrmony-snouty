"""Matcher unit tests. The matcher must be cheap and side-effect-free."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import write_synthetic_snouty
from zarrmony_snouty import SnoutyReader
from zarrmony_snouty._metadata import SnoutyMetadataError
from zarrmony_snouty.match import REQUIRED_SIDECAR_KEYS, SIDECAR_KEY_QUORUM, match


def test_matches_snap_subdir(synthetic_snouty) -> None:
    assert match(synthetic_snouty.dir) == 100


@pytest.mark.parametrize("suffix", ["_ht_sols_snap", "_ht_sols_acquire"])
def test_matches_both_gui_verbs(tmp_path: Path, suffix: str) -> None:
    fixture = write_synthetic_snouty(tmp_path, subdir_name=f"2026-07-14_10-15-35_000{suffix}")
    assert match(fixture.dir) == 100


# An operator names a scripted run by hand, so the name carries no reliable
# token. These four shapes all appear on the read-only share (#34).
@pytest.mark.parametrize(
    "name",
    [
        "2025-11-06_17-02-15_000_ht_sols_acquisition_template",
        "2025-10-16_13-43-33_000_ht_sols_gui_snap",
        "2024-06-28_10-13-04_000_ht_sols_grid",
        "2023-11-22_15-34-25_000_ht_sols_tile",
    ],
)
def test_matches_operator_named_acquisition(tmp_path: Path, name: str) -> None:
    fixture = write_synthetic_snouty(tmp_path, subdir_name=name)
    assert match(fixture.dir) == 100


def _drop_sidecar_keys(acquisition_dir: Path, *keys: str) -> None:
    sidecar = next((acquisition_dir / "metadata").glob("*.txt"))
    kept = [
        line
        for line in sidecar.read_text().splitlines()
        if not any(line.startswith(f"{key}:") for key in keys)
    ]
    sidecar.write_text("\n".join(kept) + "\n")


@pytest.mark.parametrize("missing", sorted(REQUIRED_SIDECAR_KEYS))
def test_matches_despite_one_absent_key(tmp_path: Path, missing: str) -> None:
    """A damaged sidecar is still Snouty, so the matcher says yes.

    The reader then raises ``SnoutyMetadataError`` naming the absent key. If
    the matcher said no instead, zarrmony would fall through to bioio and
    report ``UnsupportedFileFormatError``, which names nothing useful.
    """
    fixture = write_synthetic_snouty(tmp_path, subdir_name="some_random_dir")
    _drop_sidecar_keys(fixture.dir, missing)
    assert match(fixture.dir) == 100


def test_damaged_sidecar_reaches_the_reader_and_names_the_key(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, subdir_name="some_random_dir")
    _drop_sidecar_keys(fixture.dir, "voxel_aspect_ratio")
    assert match(fixture.dir) == 100
    with pytest.raises(SnoutyMetadataError, match="voxel_aspect_ratio"):
        SnoutyReader(fixture.dir)


def test_rejects_sidecar_below_the_key_quorum(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, subdir_name="some_random_dir")
    doomed = sorted(REQUIRED_SIDECAR_KEYS)[: len(REQUIRED_SIDECAR_KEYS) - SIDECAR_KEY_QUORUM + 1]
    _drop_sidecar_keys(fixture.dir, *doomed)
    assert match(fixture.dir) is None


def test_rejects_foreign_vendor_sidecar(tmp_path: Path) -> None:
    # Another vendor's data/ + metadata/ layout must not be claimed as Snouty,
    # even when its directory name carries the GUI suffix.
    d = tmp_path / "2026-07-14_10-15-35_000_ht_sols_snap"
    (d / "data").mkdir(parents=True)
    (d / "metadata").mkdir()
    (d / "data" / "x.tif").write_bytes(b"II*\x00")
    (d / "metadata" / "x.txt").write_text("exposure: 10\nbinning: 1\nheight_px: 600\n")
    assert match(d) is None


def test_reads_only_one_sidecar(tmp_path: Path, monkeypatch) -> None:
    """A 384-well plate holds thousands of sidecars on a network mount."""
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=5)
    reads: list[Path] = []
    original = Path.read_text

    def counting_read_text(self: Path, *args, **kwargs):
        reads.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    assert match(fixture.dir) == 100
    assert len(reads) == 1


def test_rejects_non_existent_path(tmp_path: Path) -> None:
    assert match(tmp_path / "nope") is None


def test_rejects_file_at_path(tmp_path: Path) -> None:
    p = tmp_path / "not_a_dir.txt"
    p.write_text("hello")
    assert match(p) is None


def test_rejects_dir_whose_sidecar_is_not_snouty(tmp_path: Path) -> None:
    # The name is free text and the sidecar is not a Snouty sidecar, so there
    # is no evidence either way. The matcher answers no.
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
