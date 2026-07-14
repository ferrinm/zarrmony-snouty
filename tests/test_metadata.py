"""Tests for the metadata sidecar parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from zarrmony_snouty._metadata import (
    TIMESTAMP_STRIP_PX,
    SnoutyMetadataError,
    parse_metadata_dir,
    parse_metadata_file,
)


def test_parses_synthetic_sidecar(synthetic_snouty) -> None:
    meta = parse_metadata_dir(synthetic_snouty.dir / "metadata")
    assert meta.channels == synthetic_snouty.channels
    assert meta.size_t == 1
    assert meta.size_z == synthetic_snouty.size_z
    assert meta.size_y == synthetic_snouty.size_y
    assert meta.size_x == synthetic_snouty.size_x
    assert meta.sample_px_um == pytest.approx(synthetic_snouty.sample_px_um)
    assert meta.scan_step_size_um == pytest.approx(synthetic_snouty.scan_step_size_um)
    assert meta.voxel_aspect_ratio == pytest.approx(synthetic_snouty.voxel_aspect_ratio)
    assert meta.scan_step_size_px == pytest.approx(synthetic_snouty.scan_step_size_px)
    assert meta.timestamp_strip_px == TIMESTAMP_STRIP_PX


def test_raw_text_is_verbatim(synthetic_snouty) -> None:
    meta = parse_metadata_dir(synthetic_snouty.dir / "metadata")
    on_disk = (synthetic_snouty.dir / "metadata" / "snap.txt").read_text()
    assert meta.raw_text == on_disk


def test_raw_dict_preserves_all_keys(synthetic_snouty) -> None:
    meta = parse_metadata_dir(synthetic_snouty.dir / "metadata")
    # non-required keys survive the round-trip so downstream audit gets them
    assert meta.raw["tilt_deg"] == pytest.approx(55.0)
    assert meta.raw["autofocus_enabled"] is False
    assert meta.raw["display"] is True


def test_missing_metadata_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(SnoutyMetadataError, match="does not exist"):
        parse_metadata_dir(tmp_path / "nope")


def test_empty_metadata_dir_raises(tmp_path: Path) -> None:
    (tmp_path / "metadata").mkdir()
    with pytest.raises(SnoutyMetadataError, match="no .txt files"):
        parse_metadata_dir(tmp_path / "metadata")


def test_missing_required_key_raises(tmp_path: Path) -> None:
    p = tmp_path / "sidecar.txt"
    p.write_text("channels_per_slice: ('LED',)\n")
    with pytest.raises(SnoutyMetadataError, match="missing required key"):
        parse_metadata_file(p)


def test_height_px_smaller_than_timestamp_strip_raises(tmp_path: Path) -> None:
    p = tmp_path / "sidecar.txt"
    p.write_text(
        "channels_per_slice: ('LED',)\n"
        "slices_per_volume: 2\n"
        "height_px: 4\n"
        "width_px: 8\n"
        "volumes_per_buffer: 1\n"
        "sample_px_um: 0.1755\n"
        "scan_step_size_um: 2.14\n"
        "voxel_aspect_ratio: 9.997\n"
        "scan_step_size_px: 7.0\n"
    )
    with pytest.raises(SnoutyMetadataError, match="PCO timestamp strip"):
        parse_metadata_file(p)


def test_coerces_bool_int_float_and_tuple(tmp_path: Path) -> None:
    p = tmp_path / "sidecar.txt"
    p.write_text(
        "channels_per_slice: ('488', '561')\n"
        "slices_per_volume: 3\n"
        "height_px: 100\n"
        "width_px: 200\n"
        "volumes_per_buffer: 1\n"
        "sample_px_um: 0.5\n"
        "scan_step_size_um: 1.5\n"
        "voxel_aspect_ratio: 3.0\n"
        "scan_step_size_px: 2.0\n"
        "some_bool: True\n"
        "some_int: 42\n"
        "some_neg_float: -1.5\n"
        "some_tuple: (1, 2, 3)\n"
    )
    meta = parse_metadata_file(p)
    assert meta.raw["some_bool"] is True
    assert meta.raw["some_int"] == 42
    assert meta.raw["some_neg_float"] == pytest.approx(-1.5)
    assert meta.raw["some_tuple"] == (1, 2, 3)


def test_picks_first_sidecar_by_mtime(tmp_path: Path) -> None:
    import os
    import time

    d = tmp_path / "metadata"
    d.mkdir()

    older = d / "000000.txt"
    older.write_text(
        "channels_per_slice: ('LED',)\n"
        "slices_per_volume: 2\n"
        "height_px: 100\n"
        "width_px: 200\n"
        "volumes_per_buffer: 1\n"
        "sample_px_um: 0.1\n"
        "scan_step_size_um: 1.0\n"
        "voxel_aspect_ratio: 2.0\n"
        "scan_step_size_px: 3.0\n"
    )
    time.sleep(0.01)
    newer = d / "000001.txt"
    newer.write_text(
        "channels_per_slice: ('OTHER',)\n"
        "slices_per_volume: 4\n"
        "height_px: 100\n"
        "width_px: 200\n"
        "volumes_per_buffer: 1\n"
        "sample_px_um: 0.9\n"
        "scan_step_size_um: 9.0\n"
        "voxel_aspect_ratio: 8.0\n"
        "scan_step_size_px: 7.0\n"
    )
    # bump mtimes explicitly to be robust against filesystems with coarse mtime
    os.utime(older, (older.stat().st_atime, 1_000.0))
    os.utime(newer, (newer.stat().st_atime, 2_000.0))

    meta = parse_metadata_dir(d)
    assert meta.channels == ("LED",)
    assert meta.size_z == 2
