"""Tests for the multiwell-plate reader (#6).

The plate map lives in the ``data/`` filenames and nowhere else, so most of
these tests are about one question: which file belongs to which well, and
which well belongs to which plate. ADR-0003 records the decisions they pin.
"""

from __future__ import annotations

import os
import shutil
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from zarrmony.readers.plate import Acquisition
from zarrmony.writers.plate import validate_plate_layout

from tests.conftest import PlateSpec, write_synthetic_snouty
from zarrmony_snouty import _open_plate, _pixels
from zarrmony_snouty import plate as plate_module
from zarrmony_snouty.adapter import SnoutyDataError, SnoutyModeError, SnoutyReader
from zarrmony_snouty.match import match_plate
from zarrmony_snouty.plate import (
    SnoutyPlateFormatError,
    SnoutyPlateFormatWarning,
    SnoutyPlateReader,
)

# 2x2 wells, 2x2 fields — the shape the acceptance criteria name.
FOUR_WELLS_FOUR_FIELDS = PlateSpec(
    grammar="A",
    wells=((0, 0), (0, 1), (1, 0), (1, 1)),
    fields=((0, 0), (0, 1), (1, 0), (1, 1)),
)


def first_pixel_of_each_timepoint(reader: SnoutyPlateReader) -> list[int]:
    """One distinguishing pixel per timepoint of the active scene, in T order."""
    computed = reader.xarray_dask_data.data.compute()
    return [int(computed[t, 0, 0, 0, 0]) for t in range(computed.shape[0])]


# --------------------------------------------------------------------------
# Scenes
# --------------------------------------------------------------------------


def test_grammar_a_gives_one_scene_per_field(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=FOUR_WELLS_FOUR_FIELDS)
    reader = SnoutyPlateReader(fixture.dir)

    name = fixture.dir.name
    assert reader.scenes == [
        f"{name}__A01r00c00",
        f"{name}__A01r00c01",
        f"{name}__A01r01c00",
        f"{name}__A01r01c01",
        f"{name}__A02r00c00",
        f"{name}__A02r00c01",
        f"{name}__A02r01c00",
        f"{name}__A02r01c01",
        f"{name}__B01r00c00",
        f"{name}__B01r00c01",
        f"{name}__B01r01c00",
        f"{name}__B01r01c01",
        f"{name}__B02r00c00",
        f"{name}__B02r00c01",
        f"{name}__B02r01c00",
        f"{name}__B02r01c01",
    ]
    assert reader.layout_hint == "plate"


def test_grammar_b_names_a_scene_after_the_canonical_well(tmp_path: Path) -> None:
    """``r00c00`` is a well under grammar B, and ``A01`` is its OME-NGFF name."""
    fixture = write_synthetic_snouty(tmp_path, plate=PlateSpec(grammar="B", wells=((0, 0), (1, 2))))
    reader = SnoutyPlateReader(fixture.dir)
    assert reader.scenes == [f"{fixture.dir.name}__A01", f"{fixture.dir.name}__B03"]


def test_fields_sort_by_well_then_by_field(tmp_path: Path) -> None:
    """Both grammars snake, and the snake carries no meaning."""
    fixture = write_synthetic_snouty(
        tmp_path,
        plate=PlateSpec(
            grammar="A",
            wells=((1, 1), (0, 0)),
            fields=((1, 1), (1, 0), (0, 1), (0, 0)),
        ),
    )
    reader = SnoutyPlateReader(fixture.dir)

    name = fixture.dir.name
    assert reader.scenes == [
        f"{name}__A01r00c00",
        f"{name}__A01r00c01",
        f"{name}__A01r01c00",
        f"{name}__A01r01c01",
        f"{name}__B02r00c00",
        f"{name}__B02r00c01",
        f"{name}__B02r01c00",
        f"{name}__B02r01c01",
    ]
    assert [(f.row, f.column, f.field_name) for f in reader.plate_layout.fields] == [
        ("A", "01", "r00c00"),
        ("A", "01", "r00c01"),
        ("A", "01", "r01c00"),
        ("A", "01", "r01c01"),
        ("B", "02", "r00c00"),
        ("B", "02", "r00c01"),
        ("B", "02", "r01c00"),
        ("B", "02", "r01c01"),
    ]


def test_set_scene_selects_that_field(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(
        tmp_path, plate=PlateSpec(grammar="B", wells=((0, 0), (0, 1), (0, 2)))
    )
    reader = SnoutyPlateReader(fixture.dir, mode="raw")
    for index in range(len(reader.scenes)):
        reader.set_scene(index)
        # Write order equals sorted order here, so field index is scene index.
        assert first_pixel_of_each_timepoint(reader) == [fixture.plate_value_for(index, 0, 0)]


def test_set_scene_rejects_an_out_of_range_index(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=PlateSpec(grammar="B"))
    reader = SnoutyPlateReader(fixture.dir)
    with pytest.raises(IndexError, match="scene index 1 out of range"):
        reader.set_scene(1)


# --------------------------------------------------------------------------
# Parsing is strict
# --------------------------------------------------------------------------


def test_mixed_grammars_raise(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=FOUR_WELLS_FOUR_FIELDS)
    (fixture.dir / "data" / "000000_r05c05.tif").write_bytes(b"II*\x00")
    with pytest.raises(SnoutyDataError, match="mixes the two plate filename grammars"):
        SnoutyPlateReader(fixture.dir)


def test_a_tif_matching_neither_grammar_raises(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=FOUR_WELLS_FOUR_FIELDS)
    (fixture.dir / "data" / "operator_notes.tif").write_bytes(b"II*\x00")
    with pytest.raises(SnoutyDataError, match="matches neither plate filename grammar"):
        SnoutyPlateReader(fixture.dir)


def test_a_zero_column_under_grammar_a_raises(tmp_path: Path) -> None:
    """Grammar A columns are 1-based, so ``A00`` has no meaning."""
    fixture = write_synthetic_snouty(tmp_path, plate=PlateSpec(grammar="A"))
    (fixture.dir / "data" / "000000_A00r00c00.tif").write_bytes(b"II*\x00")
    with pytest.raises(SnoutyDataError, match="column 00"):
        SnoutyPlateReader(fixture.dir)


def test_non_tif_entries_in_data_are_ignored(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=FOUR_WELLS_FOUR_FIELDS)
    (fixture.dir / "data" / "notes.txt").write_text("operator notes\n")
    assert len(SnoutyPlateReader(fixture.dir).scenes) == 16


def test_an_empty_data_directory_raises(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=PlateSpec(grammar="A"))
    for tif in (fixture.dir / "data").glob("*.tif"):
        tif.unlink()
    with pytest.raises(SnoutyDataError, match="no .tif files"):
        SnoutyPlateReader(fixture.dir)


def test_an_unknown_mode_raises(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=PlateSpec(grammar="A"))
    with pytest.raises(SnoutyModeError, match="unknown SnoutyPlateReader mode"):
        SnoutyPlateReader(fixture.dir, mode="sideways")


# --------------------------------------------------------------------------
# Grid inference
# --------------------------------------------------------------------------


def test_two_by_two_wells_snap_to_the_six_well_plate(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=FOUR_WELLS_FOUR_FIELDS)
    layout = SnoutyPlateReader(fixture.dir).plate_layout

    assert layout.name == fixture.dir.name
    assert layout.rows == ["A", "B"]
    assert layout.columns == ["01", "02", "03"]
    assert layout.acquisitions == [Acquisition(id=0, name=fixture.dir.name)]
    assert len(layout.fields) == 16
    per_well = Counter((f.row, f.column) for f in layout.fields)
    assert per_well == {("A", "01"): 4, ("A", "02"): 4, ("B", "01"): 4, ("B", "02"): 4}


def test_a_sparse_plate_snaps_to_384_wells(tmp_path: Path) -> None:
    """20 wells in two columns. The observed extent is 12 by 2, and the plate
    is a 384-well plate."""
    wells = tuple((row, 10) for row in range(1, 11)) + tuple((row, 11) for row in range(3, 13))
    fixture = write_synthetic_snouty(tmp_path, plate=PlateSpec(grammar="B", wells=wells))
    reader = SnoutyPlateReader(fixture.dir)
    layout = reader.plate_layout

    assert len(reader.scenes) == 20
    assert len(layout.rows) == 16
    assert len(layout.columns) == 24
    assert layout.rows[:3] == ["A", "B", "C"]
    assert layout.columns[-1] == "24"
    assert len({(f.row, f.column) for f in layout.fields}) == 20


def test_unimaged_wells_keep_their_row_and_column_names(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=PlateSpec(grammar="A", wells=((0, 0), (1, 1))))
    layout = SnoutyPlateReader(fixture.dir).plate_layout

    imaged = {(f.row, f.column) for f in layout.fields}
    assert imaged == {("A", "01"), ("B", "02")}
    # B01, A02, A03 and B03 are unimaged, and their names are still here.
    assert layout.rows == ["A", "B"]
    assert layout.columns == ["01", "02", "03"]


def test_plate_format_overrides_the_lookup(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=FOUR_WELLS_FOUR_FIELDS)
    layout = SnoutyPlateReader(fixture.dir, plate_format=96).plate_layout
    assert layout.rows == [chr(ord("A") + i) for i in range(8)]
    assert layout.columns == [f"{i:02d}" for i in range(1, 13)]


def test_plate_format_rejects_a_non_standard_well_count(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=PlateSpec(grammar="A"))
    with pytest.raises(SnoutyPlateFormatError, match="not a standard plate format"):
        SnoutyPlateReader(fixture.dir, plate_format=100)


def test_plate_format_rejects_a_format_smaller_than_the_wells(tmp_path: Path) -> None:
    """A 6-well plate is 2 by 3, and these filenames name row D."""
    fixture = write_synthetic_snouty(tmp_path, plate=PlateSpec(grammar="A", wells=((0, 0), (3, 0))))
    with pytest.raises(SnoutyPlateFormatError, match="plate_format=6"):
        SnoutyPlateReader(fixture.dir, plate_format=6)


def test_wells_past_every_standard_format_warn_and_fall_back(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(
        tmp_path, plate=PlateSpec(grammar="B", wells=((0, 0), (40, 1)))
    )
    with pytest.warns(SnoutyPlateFormatWarning, match="no standard plate format contains"):
        layout = SnoutyPlateReader(fixture.dir).plate_layout

    assert len(layout.rows) == 41
    assert layout.rows[25:28] == ["Z", "AA", "AB"]
    assert layout.columns == ["01", "02"]


def test_the_layout_satisfies_the_zarrmony_writer(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=FOUR_WELLS_FOUR_FIELDS)
    reader = SnoutyPlateReader(fixture.dir)
    validate_plate_layout(reader.plate_layout, n_scenes=len(reader.scenes))
    assert [f.scene_index for f in reader.plate_layout.fields] == list(range(16))
    assert all(f.acquisition_id is None for f in reader.plate_layout.fields)


def test_grammar_b_fields_carry_no_field_name(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=PlateSpec(grammar="B", wells=((0, 0), (0, 1))))
    layout = SnoutyPlateReader(fixture.dir).plate_layout
    assert [f.field_name for f in layout.fields] == [None, None]


# --------------------------------------------------------------------------
# Composition with the flat reader's pixel path
# --------------------------------------------------------------------------


def _plate_copy_of(flat_dir: Path, root: Path) -> Path:
    """Re-file one flat acquisition under a plate filename, byte for byte.

    Both readers then get identical input, so a difference in their output is
    a difference in the pixel path and nothing else.
    """
    plate_dir = root / "2026-07-14_10-15-35_000_copied_run"
    (plate_dir / "data").mkdir(parents=True)
    (plate_dir / "metadata").mkdir()
    shutil.copy(flat_dir / "data" / "snap.tif", plate_dir / "data" / "000000_A01r00c00.tif")
    shutil.copy(flat_dir / "metadata" / "snap.txt", plate_dir / "metadata" / "000000_A01r00c00.txt")
    return plate_dir


@pytest.mark.parametrize("mode", ["raw", "desheared", "traditional"])
def test_each_mode_matches_the_flat_reader_on_the_same_volume(tmp_path: Path, mode) -> None:
    flat = write_synthetic_snouty(tmp_path / "flat")
    plate_dir = _plate_copy_of(flat.dir, tmp_path / "plate")

    plate_reader = SnoutyPlateReader(plate_dir, mode=mode)
    flat_reader = SnoutyReader(flat.dir, mode=mode)

    assert plate_reader.xarray_dask_data.dims == ("T", "C", "Z", "Y", "X")
    assert plate_reader.xarray_dask_data.shape == flat_reader.xarray_dask_data.shape
    assert plate_reader.physical_pixel_sizes == flat_reader.physical_pixel_sizes
    assert np.array_equal(
        plate_reader.xarray_dask_data.data.compute(),
        flat_reader.xarray_dask_data.data.compute(),
    ), "a plate field and a flat scene must go through one pixel path"


def test_the_cpu_engine_runs(tmp_path: Path) -> None:
    """``cpu`` is the one engine value that resolves to the CPU on every host.

    ``auto`` does not, because it reads the host. It has its own test below,
    which fixes the device leaves rather than trusting them (#43).
    """
    fixture = write_synthetic_snouty(tmp_path, plate=PlateSpec(grammar="A"))
    reader = SnoutyPlateReader(fixture.dir, mode="traditional", engine="cpu")
    assert reader.engine_used == "cpu"
    assert reader.acquisition_audit["microscope"] == "HT-SOLS"
    assert reader.xarray_dask_data.data.compute().any()


def test_auto_falls_back_to_the_cpu_and_the_plate_still_converts(
    tmp_path: Path, fake_device
) -> None:
    """The plate reader shares one resolver with the flat and session readers,
    so ``tests/test_engine.py`` owns the decision table. What this adds is the
    plate shim: the kwarg reaches that resolver, the fallback reason survives
    on the reader, and the pixels still come out.

    The device leaves are fixed, so the answer is the same on a GPU host.
    """
    fixture = write_synthetic_snouty(tmp_path, plate=PlateSpec(grammar="A"))
    fake_device(cupy_available=False, free_bytes=None)

    reader = SnoutyPlateReader(fixture.dir, mode="traditional", engine="auto")

    assert reader.engine_used == "cpu"
    assert reader.engine_fallback_reason == "cupy not installed"
    assert reader.acquisition_audit["microscope"] == "HT-SOLS"
    assert reader.xarray_dask_data.data.compute().any()


def test_three_timepoints_concatenate_on_the_t_axis(tmp_path: Path, monkeypatch) -> None:
    """Synthetic only. Every plate acquisition on the share has one timepoint.

    The directory listing is forced into a wrong order, so the assertion rests
    on the burned-in camera stamp and not on the filesystem (#23).
    """
    fixture = write_synthetic_snouty(
        tmp_path,
        plate=PlateSpec(grammar="A", wells=((0, 0), (0, 1))),
        n_timepoints=3,
    )
    for path in sorted(fixture.dir.rglob("*")):
        if path.is_file():
            os.utime(path, (1_000_000_000.0, 1_000_000_000.0))

    real_glob = Path.glob

    def reversed_glob(self, pattern, **kwargs):
        return iter(sorted(real_glob(self, pattern, **kwargs), reverse=True))

    monkeypatch.setattr(Path, "glob", reversed_glob)

    reader = SnoutyPlateReader(fixture.dir, mode="raw")
    assert len(reader.scenes) == 2
    for field_index in range(2):
        reader.set_scene(field_index)
        assert reader.xarray_dask_data.shape[0] == 3
        assert first_pixel_of_each_timepoint(reader) == [
            fixture.plate_value_for(field_index, 0, t) for t in range(3)
        ]


def test_a_one_timepoint_plate_opens_no_tif_to_order_time(tmp_path: Path, monkeypatch) -> None:
    """A 384-well plate holds 3456 files, and one open costs seconds on the
    share. With one file per field, the stamp decides nothing."""
    fixture = write_synthetic_snouty(tmp_path, plate=FOUR_WELLS_FOUR_FIELDS)

    def fail(path):
        raise AssertionError(f"the reader read the burned-in stamp of {path}")

    monkeypatch.setattr(_pixels._pco_timestamp, "read_stamp", fail)
    assert len(SnoutyPlateReader(fixture.dir).scenes) == 16


def test_the_sidecar_comes_from_the_first_data_file(tmp_path: Path, monkeypatch) -> None:
    """Scanning ``metadata/`` costs one stat per sidecar, and a 384-well plate
    has 3456 of them. The reader names the file it wants instead."""
    fixture = write_synthetic_snouty(tmp_path, plate=FOUR_WELLS_FOUR_FIELDS)

    def fail(metadata_dir):
        raise AssertionError(f"the reader scanned {metadata_dir} for a sidecar")

    monkeypatch.setattr(plate_module, "parse_metadata_dir", fail)
    assert SnoutyPlateReader(fixture.dir).channel_names == list(fixture.channels)


def test_a_missing_first_sidecar_falls_back_to_the_directory_scan(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=FOUR_WELLS_FOUR_FIELDS)
    first = sorted((fixture.dir / "data").glob("*.tif"))[0]
    (fixture.dir / "metadata" / f"{first.stem}.txt").unlink()
    assert SnoutyPlateReader(fixture.dir).channel_names == list(fixture.channels)


def test_four_channels_reach_the_c_axis(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(
        tmp_path,
        plate=PlateSpec(grammar="A", wells=((0, 0), (0, 1))),
        channels=("405", "488", "561", "640"),
    )
    reader = SnoutyPlateReader(fixture.dir, mode="raw")
    assert reader.channel_names == ["405", "488", "561", "640"]
    data = reader.xarray_dask_data
    assert data.shape[1] == 4
    assert list(data.coords["C"].values) == ["405", "488", "561", "640"]
    computed = data.data.compute()
    for c in range(4):
        assert computed[0, c, 0, 0, 0] == fixture.plate_value_for(0, 0, 0, c)


def test_the_plugin_shim_reads_the_mode_env_var(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZARRMONY_SNOUTY_MODE", "raw")
    fixture = write_synthetic_snouty(tmp_path, plate=PlateSpec(grammar="A"))
    reader = _open_plate(fixture.dir)
    assert reader.xarray_dask_data.shape == (
        1,
        1,
        fixture.size_z,
        fixture.size_y,
        fixture.size_x,
    )


def test_the_reader_surfaces_the_sidecar_text(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, plate=PlateSpec(grammar="A"))
    reader = SnoutyPlateReader(fixture.dir)
    assert "slices_per_volume" in reader.metadata
    assert reader.dtype == np.dtype("uint16")
    reader.close()


# --------------------------------------------------------------------------
# Real-data smoke — opt-in, skipped when unset
# --------------------------------------------------------------------------

REAL_PLATE_A_ENV_VAR = "ZARRMONY_SNOUTY_REAL_PLATE_A_DIR"
REAL_PLATE_B_SPARSE_ENV_VAR = "ZARRMONY_SNOUTY_REAL_PLATE_B_SPARSE_DIR"


def _real_plate_dir(env_var: str) -> Path:
    env_path = os.environ.get(env_var)
    if not env_path:
        pytest.skip(f"{env_var} not set; skipping real-data smoke")
    real_dir = Path(env_path)
    if not real_dir.is_dir():
        pytest.skip(f"{env_var}={env_path} is not a directory; skipping")
    return real_dir


def test_real_grammar_a_plate_smoke() -> None:
    """Smoke test on a real full 384-well grammar-A acquisition.

    Point ``ZARRMONY_SNOUTY_REAL_PLATE_A_DIR`` at a plate run whose ``data/``
    holds ``NNNNNN_<Row><CC>r<NN>c<NN>.tif`` files: 384 wells, 9 fields per
    well, 4 channels, one timepoint. Skipped when unset so CI and forks stay
    clean; kept out of the tree because real acquisition paths embed
    colleague names and sample IDs.

    The smoke stops at the layout, one ``set_scene``, and one array read. A
    full conversion of 3456 fields is a manual run, not a test.
    """
    real_dir = _real_plate_dir(REAL_PLATE_A_ENV_VAR)

    assert match_plate(real_dir) == 200
    reader = SnoutyPlateReader(real_dir)
    layout = reader.plate_layout

    assert len(reader.scenes) == 3456
    wells = {(f.row, f.column) for f in layout.fields}
    assert len(wells) == 384
    assert len(layout.fields) // len(wells) == 9
    assert len(layout.rows) == 16
    assert len(layout.columns) == 24
    assert len(reader.channel_names) == 4

    reader.set_scene(0)
    first = reader.xarray_dask_data.isel(T=0).data.compute()
    assert first.dtype == np.uint16
    assert first.any()


def test_real_sparse_grammar_b_plate_smoke() -> None:
    """Smoke test on the real sparse grammar-B acquisition.

    Point ``ZARRMONY_SNOUTY_REAL_PLATE_B_SPARSE_DIR`` at the plate run whose
    ``data/`` holds ``NNNNNN_r<NN>c<NN>.tif`` files for 20 wells of a 384-well
    plate. The 20 imaged wells must not shrink the reported plate.
    """
    real_dir = _real_plate_dir(REAL_PLATE_B_SPARSE_ENV_VAR)

    assert match_plate(real_dir) == 200
    reader = SnoutyPlateReader(real_dir)
    layout = reader.plate_layout

    assert len(reader.scenes) == 20
    assert len({(f.row, f.column) for f in layout.fields}) == 20
    assert len(layout.rows) == 16
    assert len(layout.columns) == 24

    reader.set_scene(0)
    first = reader.xarray_dask_data.isel(T=0).data.compute()
    assert first.dtype == np.uint16
    assert first.any()
