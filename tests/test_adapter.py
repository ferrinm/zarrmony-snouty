"""Adapter tests: shape, pixel sizes, plane content, and v0.1 guardrails."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import tifffile

from tests.conftest import write_synthetic_snouty
from zarrmony_snouty import _deshear
from zarrmony_snouty.adapter import (
    SnoutyDataError,
    SnoutyModeError,
    SnoutyMultiChannelUnsupportedError,
    SnoutyMultipositionUnsupportedError,
    SnoutyReader,
    SnoutyVolumesPerBufferUnsupportedError,
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


def test_volumes_per_buffer_greater_than_one_raises(tmp_path: Path) -> None:
    # Rewrite the sidecar's volumes_per_buffer to 2 — the on-disk .tif is
    # still a single volume, but the guardrail fires on the metadata claim
    # because we can't yet split a single-file, multi-volume buffer without
    # multi-channel support (deferred to #4).
    fixture = write_synthetic_snouty(tmp_path)
    sidecar = fixture.dir / "metadata" / "snap.txt"
    sidecar.write_text(
        sidecar.read_text().replace("volumes_per_buffer: 1", "volumes_per_buffer: 2")
    )

    with pytest.raises(SnoutyVolumesPerBufferUnsupportedError, match="volumes_per_buffer=2"):
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


def test_default_mode_is_raw(synthetic_snouty) -> None:
    default = SnoutyReader(synthetic_snouty.dir)
    explicit = SnoutyReader(synthetic_snouty.dir, mode="raw")
    assert default.xarray_dask_data.shape == explicit.xarray_dask_data.shape
    assert default.physical_pixel_sizes == explicit.physical_pixel_sizes


def test_unknown_mode_raises(synthetic_snouty) -> None:
    with pytest.raises(SnoutyModeError, match="unknown SnoutyReader mode"):
        SnoutyReader(synthetic_snouty.dir, mode="rotated")  # type: ignore[arg-type]


def test_desheared_mode_shape_and_pixel_sizes(synthetic_snouty) -> None:
    reader = SnoutyReader(synthetic_snouty.dir, mode="desheared")

    max_shift = _deshear.max_deshear_shift(
        synthetic_snouty.scan_step_size_px, synthetic_snouty.size_z
    )
    xr_da = reader.xarray_dask_data
    assert xr_da.dims == ("T", "C", "Z", "Y", "X")
    assert xr_da.shape == (
        1,
        1,
        synthetic_snouty.size_z,
        synthetic_snouty.size_y + max_shift,
        synthetic_snouty.size_x,
    )
    assert xr_da.dtype == np.uint16

    # Deshear does not change spacing.
    pps = reader.physical_pixel_sizes
    assert pps.X == pytest.approx(synthetic_snouty.sample_px_um)
    assert pps.Y == pytest.approx(synthetic_snouty.sample_px_um)
    assert pps.Z == pytest.approx(synthetic_snouty.scan_step_size_um)


def test_desheared_mode_places_planes_at_expected_y_offsets(synthetic_snouty) -> None:
    reader = SnoutyReader(synthetic_snouty.dir, mode="desheared")
    computed = reader.xarray_dask_data.data.compute()

    for z in range(synthetic_snouty.size_z):
        shift = int(np.rint(synthetic_snouty.scan_step_size_px * z))
        plane = computed[0, 0, z, :, :]
        expected_val = synthetic_snouty.value_for(z)
        assert (plane[shift : shift + synthetic_snouty.size_y, :] == expected_val).all()
        # Zero-padded above and below the shifted plane.
        if shift:
            assert (plane[:shift, :] == 0).all()
        assert (plane[shift + synthetic_snouty.size_y :, :] == 0).all()


def test_traditional_mode_shape_and_pixel_sizes(synthetic_snouty) -> None:
    reader = SnoutyReader(synthetic_snouty.dir, mode="traditional")

    y_rot, z_rot, x_out = _deshear.traditional_shape(
        synthetic_snouty.size_z,
        synthetic_snouty.size_y,
        synthetic_snouty.size_x,
        synthetic_snouty.scan_step_size_px,
        synthetic_snouty.voxel_aspect_ratio,
    )
    xr_da = reader.xarray_dask_data
    assert xr_da.dims == ("T", "C", "Z", "Y", "X")
    assert xr_da.shape == (1, 1, y_rot, z_rot, x_out)
    assert xr_da.dtype == np.uint16

    pps = reader.physical_pixel_sizes
    assert pps.X == pytest.approx(synthetic_snouty.sample_px_um)
    assert pps.Y == pytest.approx(synthetic_snouty.sample_px_um)
    assert pps.Z == pytest.approx(
        synthetic_snouty.sample_px_um * synthetic_snouty.voxel_aspect_ratio
    )


def test_traditional_mode_computes_without_error(synthetic_snouty) -> None:
    reader = SnoutyReader(synthetic_snouty.dir, mode="traditional")
    computed = reader.xarray_dask_data.data.compute()
    assert computed.dtype == np.uint16
    # Some non-zero content should survive the rotate + crop.
    assert computed.any()


def test_real_tiff_shape_matches_metadata(synthetic_snouty) -> None:
    on_disk = tifffile.imread(synthetic_snouty.dir / "data" / "snap.tif")
    # Sanity check on the fixture itself — height is BEFORE crop.
    assert on_disk.shape == (
        synthetic_snouty.size_z,
        synthetic_snouty.height_px,
        synthetic_snouty.size_x,
    )


def test_multi_timepoint_shape_and_dtype(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(
        tmp_path,
        subdir_name="2026-07-14_10-24-15_000_ht_sols_acquire",
        n_timepoints=3,
    )
    reader = SnoutyReader(fixture.dir)
    xr_da = reader.xarray_dask_data
    assert xr_da.dims == ("T", "C", "Z", "Y", "X")
    assert xr_da.shape == (3, 1, fixture.size_z, fixture.size_y, fixture.size_x)
    assert xr_da.dtype == np.uint16


def test_multi_timepoint_one_dask_chunk_per_timepoint(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=3)
    reader = SnoutyReader(fixture.dir)
    # AC: one dask chunk per timepoint. da.stack(axis=0) of three (Z,Y,X)
    # delayeds yields T-axis chunks of (1, 1, 1) with a single chunk in every
    # other axis.
    chunks = reader.xarray_dask_data.data.chunks
    assert chunks[0] == (1, 1, 1)
    for other_axis_chunks in chunks[1:]:
        assert len(other_axis_chunks) == 1


def test_multi_timepoint_preserves_per_plane_content_and_order(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=3)
    reader = SnoutyReader(fixture.dir)
    computed = reader.xarray_dask_data.data.compute()

    for t in range(fixture.n_timepoints):
        for z in range(fixture.size_z):
            plane = computed[t, 0, z, :, :]
            assert (plane == fixture.value_for(z, t)).all()
    assert (computed != 9999).all()


def test_multi_timepoint_desheared_mode(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=2)
    reader = SnoutyReader(fixture.dir, mode="desheared")
    max_shift = _deshear.max_deshear_shift(fixture.scan_step_size_px, fixture.size_z)
    xr_da = reader.xarray_dask_data
    assert xr_da.shape == (2, 1, fixture.size_z, fixture.size_y + max_shift, fixture.size_x)
    # Deshear per timepoint places planes at the same Y offsets each T.
    computed = xr_da.data.compute()
    for t in range(fixture.n_timepoints):
        for z in range(fixture.size_z):
            shift = int(np.rint(fixture.scan_step_size_px * z))
            plane = computed[t, 0, z, :, :]
            assert (plane[shift : shift + fixture.size_y, :] == fixture.value_for(z, t)).all()


def test_multi_timepoint_ordering_uses_mtime_matching_filename_order(tmp_path: Path) -> None:
    # snouty-folder sorts by mtime; zero-padded filenames make mtime and
    # filename orders equivalent for correctly-written acquisitions. Confirm
    # our reader agrees with filename order (i.e. 000000 → t=0, 000001 → t=1).
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=2)
    reader = SnoutyReader(fixture.dir)
    computed = reader.xarray_dask_data.data.compute()
    # value_for(z=0, t=0) is distinct from value_for(z=0, t=1) — checking the
    # first cropped Y row of z=0 pins down which file landed where.
    assert computed[0, 0, 0, 0, 0] == fixture.value_for(0, 0)
    assert computed[1, 0, 0, 0, 0] == fixture.value_for(0, 1)


REAL_MULTI_T_ENV_VAR = "ZARRMONY_SNOUTY_REAL_MULTI_T_DIR"


def test_real_multi_timepoint_smoke() -> None:
    """Smoke test on a real single-channel, single-position, multi-timepoint acquisition.

    Point ``ZARRMONY_SNOUTY_REAL_MULTI_T_DIR`` at any ``_ht_sols_acquire``
    directory with ``channels_per_slice: ('<one>',)``, no ``_pNNNNNN`` files,
    ``volumes_per_buffer: 1``, and more than one ``.tif`` under ``data/``.
    Skipped when unset so CI and forks stay clean; kept out of the tree
    because real acquisition paths embed colleague names and sample IDs.

    The AC's named fixture (``2026-07-14_10-24-15_000_ht_sols_acquire``) is
    multi-channel, so its full ``(T, C, Z, Y, X)`` smoke waits on #4.
    """
    env_path = os.environ.get(REAL_MULTI_T_ENV_VAR)
    if not env_path:
        pytest.skip(f"{REAL_MULTI_T_ENV_VAR} not set; skipping real-data smoke")
    real_dir = Path(env_path)
    if not real_dir.is_dir():
        pytest.skip(f"{REAL_MULTI_T_ENV_VAR}={env_path} is not a directory; skipping")

    reader = SnoutyReader(real_dir)
    xr_da = reader.xarray_dask_data
    n_files = len(list((real_dir / "data").glob("*.tif")))
    assert n_files > 1, f"{env_path} has only {n_files} .tif file(s); need >1 for multi-T smoke"
    assert xr_da.dims == ("T", "C", "Z", "Y", "X")
    assert xr_da.shape[0] == n_files
    assert xr_da.shape[1] == 1
    assert xr_da.chunks[0] == tuple([1] * n_files)
    # Materialize just the first timepoint to keep the smoke cheap.
    first = xr_da.isel(T=0).data.compute()
    assert first.dtype == np.uint16
    assert first.any()
