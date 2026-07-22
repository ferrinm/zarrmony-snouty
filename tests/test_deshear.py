"""Unit tests for the CPU deshear + traditional-view transforms."""

from __future__ import annotations

import numpy as np

from zarrmony_snouty import _deshear


def test_max_deshear_shift_uses_z_minus_one() -> None:
    # Reference: int(rint(scan_step_size_px * (num_z - 1)))
    assert _deshear.max_deshear_shift(7.0, 4) == 21
    assert _deshear.max_deshear_shift(0.5, 10) == 4
    assert _deshear.max_deshear_shift(3.6, 5) == 14


def test_desheared_shape_pads_y_by_max_shift() -> None:
    assert _deshear.desheared_shape(4, 6, 8, 7.0) == (4, 6 + 21, 8)


def test_deshear_shifts_each_plane_by_round_scan_step_times_z() -> None:
    size_z, size_y, size_x = 4, 3, 5
    scan_step_size_px = 2.5
    volume = np.zeros((size_z, size_y, size_x), dtype=np.uint16)
    for z in range(size_z):
        volume[z, :, :] = z + 1  # distinct per-plane values

    out = _deshear.deshear_zyx(volume, scan_step_size_px)

    assert out.shape == _deshear.desheared_shape(size_z, size_y, size_x, scan_step_size_px)
    assert out.dtype == np.uint16
    for z in range(size_z):
        shift = int(np.rint(scan_step_size_px * z))
        # placed rows carry the plane's value
        assert (out[z, shift : shift + size_y, :] == z + 1).all()
        # padded rows are zeroed
        assert (out[z, :shift, :] == 0).all()
        assert (out[z, shift + size_y :, :] == 0).all()


def test_traditional_shape_matches_reference_formula() -> None:
    # Reproduce snouty_folder._load_traditional_dims exactly.
    size_z, size_y, size_x = 4, 6, 8
    scan_step_size_px, aspect = 7.0, 9.997
    final = np.arctan(scan_step_size_px / aspect)
    initial = np.arctan(scan_step_size_px)
    y_rot = int(np.rint(np.sin(initial) * size_y))
    z_rot = int(np.rint((size_z * aspect / np.cos(final)) + (np.cos(initial) * size_y / aspect)))
    assert _deshear.traditional_shape(size_z, size_y, size_x, scan_step_size_px, aspect) == (
        y_rot,
        z_rot,
        size_x,
    )


def test_traditional_zyx_returns_expected_shape_and_dtype() -> None:
    size_z, size_y, size_x = 4, 6, 8
    scan_step_size_px, aspect = 7.0, 9.997
    volume = np.arange(size_z * size_y * size_x, dtype=np.uint16).reshape(size_z, size_y, size_x)

    out = _deshear.traditional_zyx(volume, scan_step_size_px, aspect)

    assert out.shape == _deshear.traditional_shape(
        size_z, size_y, size_x, scan_step_size_px, aspect
    )
    assert out.dtype == np.uint16
