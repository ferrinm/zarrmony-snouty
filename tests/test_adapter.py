"""Adapter tests: shape, pixel sizes, plane content, and v0.1 guardrails."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import tifffile

from tests.conftest import write_synthetic_snouty
from zarrmony_snouty.adapter import (
    SnoutyDataError,
    SnoutyMultiChannelUnsupportedError,
    SnoutyMultipositionUnsupportedError,
    SnoutyMultiTimepointUnsupportedError,
    SnoutyReader,
)


def test_scenes_reports_dir_name(synthetic_snouty) -> None:
    reader = SnoutyReader(synthetic_snouty.dir)
    assert reader.scenes == [synthetic_snouty.dir.name]


def test_set_scene_out_of_range_raises(synthetic_snouty) -> None:
    reader = SnoutyReader(synthetic_snouty.dir)
    with pytest.raises(IndexError):
        reader.set_scene(1)


def test_channel_names(synthetic_snouty) -> None:
    reader = SnoutyReader(synthetic_snouty.dir)
    assert reader.channel_names == list(synthetic_snouty.channels)


def test_physical_pixel_sizes(synthetic_snouty) -> None:
    reader = SnoutyReader(synthetic_snouty.dir)
    pps = reader.physical_pixel_sizes
    assert pps.X == pytest.approx(synthetic_snouty.sample_px_um)
    assert pps.Y == pytest.approx(synthetic_snouty.sample_px_um)
    # Z is the raw skewed scan step, not the deshear/rotate orthogonal spacing
    assert pps.Z == pytest.approx(synthetic_snouty.scan_step_size_um)


def test_metadata_round_trips_verbatim(synthetic_snouty) -> None:
    reader = SnoutyReader(synthetic_snouty.dir)
    on_disk = (synthetic_snouty.dir / "metadata" / "snap.txt").read_text()
    assert reader.metadata == on_disk


def test_xarray_dims_and_shape(synthetic_snouty) -> None:
    reader = SnoutyReader(synthetic_snouty.dir)
    xr_da = reader.xarray_dask_data
    assert xr_da.dims == ("T", "C", "Z", "Y", "X")
    assert xr_da.shape == (
        1,
        1,
        synthetic_snouty.size_z,
        synthetic_snouty.size_y,
        synthetic_snouty.size_x,
    )
    assert xr_da.dtype == np.uint16


def test_xarray_crops_timestamp_strip_and_preserves_content(synthetic_snouty) -> None:
    reader = SnoutyReader(synthetic_snouty.dir)
    computed = reader.xarray_dask_data.data.compute()
    # Values in the cropped region match the per-plane fill; timestamp
    # sentinel (9999) must be gone.
    for z in range(synthetic_snouty.size_z):
        plane = computed[0, 0, z, :, :]
        assert plane.shape == (synthetic_snouty.size_y, synthetic_snouty.size_x)
        assert (plane == synthetic_snouty.value_for(z)).all()
    assert (computed != 9999).all()


def test_multiposition_raises(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(
        tmp_path,
        subdir_name="2026-07-14_10-54-45_000_ht_sols_acquire",
    )
    # Add a second position file — enough to trip the multi-position guardrail.
    src = fixture.dir / "data" / "snap.tif"
    (fixture.dir / "data" / "000000_p000000.tif").write_bytes(src.read_bytes())
    (fixture.dir / "data" / "000000_p000001.tif").write_bytes(src.read_bytes())
    src.unlink()

    with pytest.raises(SnoutyMultipositionUnsupportedError, match="multi-position"):
        SnoutyReader(fixture.dir)


def test_multi_timepoint_raises(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(
        tmp_path,
        subdir_name="2026-07-14_10-24-15_000_ht_sols_acquire",
    )
    src = fixture.dir / "data" / "snap.tif"
    (fixture.dir / "data" / "000001.tif").write_bytes(src.read_bytes())

    with pytest.raises(SnoutyMultiTimepointUnsupportedError, match="multi-timepoint"):
        SnoutyReader(fixture.dir)


def test_multi_channel_raises(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(
        tmp_path,
        channels=("488", "561"),
    )
    with pytest.raises(SnoutyMultiChannelUnsupportedError, match="multi-channel"):
        SnoutyReader(fixture.dir)


def test_empty_data_dir_raises(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path)
    (fixture.dir / "data" / "snap.tif").unlink()
    with pytest.raises(SnoutyDataError, match="no .tif files"):
        SnoutyReader(fixture.dir)


def test_real_tiff_shape_matches_metadata(synthetic_snouty) -> None:
    on_disk = tifffile.imread(synthetic_snouty.dir / "data" / "snap.tif")
    # Sanity check on the fixture itself — height is BEFORE crop.
    assert on_disk.shape == (
        synthetic_snouty.size_z,
        synthetic_snouty.height_px,
        synthetic_snouty.size_x,
    )
