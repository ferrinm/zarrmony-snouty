"""Tests for the plugin's ``open`` shim and its ``ZARRMONY_SNOUTY_MODE`` env var."""

from __future__ import annotations

import pytest

from zarrmony_snouty import _deshear, _open
from zarrmony_snouty.adapter import SnoutyModeError


def test_open_defaults_to_raw_when_env_unset(synthetic_snouty, monkeypatch) -> None:
    monkeypatch.delenv("ZARRMONY_SNOUTY_MODE", raising=False)
    reader = _open(synthetic_snouty.dir)
    assert reader.xarray_dask_data.shape == (
        1,
        1,
        synthetic_snouty.size_z,
        synthetic_snouty.size_y,
        synthetic_snouty.size_x,
    )


def test_open_reads_desheared_from_env(synthetic_snouty, monkeypatch) -> None:
    monkeypatch.setenv("ZARRMONY_SNOUTY_MODE", "desheared")
    reader = _open(synthetic_snouty.dir)
    max_shift = _deshear.max_deshear_shift(
        synthetic_snouty.scan_step_size_px, synthetic_snouty.size_z
    )
    assert reader.xarray_dask_data.shape == (
        1,
        1,
        synthetic_snouty.size_z,
        synthetic_snouty.size_y + max_shift,
        synthetic_snouty.size_x,
    )


def test_open_reads_traditional_from_env(synthetic_snouty, monkeypatch) -> None:
    monkeypatch.setenv("ZARRMONY_SNOUTY_MODE", "traditional")
    reader = _open(synthetic_snouty.dir)
    y_rot, z_rot, x_out = _deshear.traditional_shape(
        synthetic_snouty.size_z,
        synthetic_snouty.size_y,
        synthetic_snouty.size_x,
        synthetic_snouty.scan_step_size_px,
        synthetic_snouty.voxel_aspect_ratio,
    )
    assert reader.xarray_dask_data.shape == (1, 1, y_rot, z_rot, x_out)


def test_open_rejects_unknown_mode(synthetic_snouty, monkeypatch) -> None:
    monkeypatch.setenv("ZARRMONY_SNOUTY_MODE", "sideways")
    with pytest.raises(SnoutyModeError, match="unknown SnoutyReader mode"):
        _open(synthetic_snouty.dir)
