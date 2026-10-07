"""Tests for the ``ome_metadata`` property of the three Snouty readers (#39).

zarrmony reads this property once per scene. It projects the result into the
OME-XML it writes beside the array, into the per-scene ``channels`` audit
block, and into ``acquisition.date``. A reader without it converts, but every
scene warns and the three audit surfaces go missing.

The hard part is not the OME object. It is the sizes. The sidecar records the
raw shape and forces ``volumes_per_buffer`` to 1, so neither sidecar size is
the shape the writer writes. These tests compare the OME ``Pixels`` against
``xarray_dask_data`` directly, in every mode, so the two cannot drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from ome_types.model import Pixels

from tests.conftest import SIDECAR_ACQUISITION_DATE, PlateSpec, write_synthetic_snouty
from zarrmony_snouty import SnoutyPlateReader, SnoutyReader, SnoutySessionReader
from zarrmony_snouty._pixels import _MODES


def _only_image_pixels(reader: Any) -> Pixels:
    """The ``Pixels`` of the reader's one Image, after checking that it has one."""
    ome = reader.ome_metadata
    assert len(ome.images) == 1, "one Image for the active scene, never one per scene"
    return ome.images[0].pixels


@pytest.mark.parametrize("mode", _MODES)
def test_pixels_sizes_equal_the_written_array(tmp_path: Path, mode: str) -> None:
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=3, channels=("LED", "488"))
    reader = SnoutyReader(fixture.dir, mode=mode)

    sizes = reader.xarray_dask_data.sizes
    pixels = _only_image_pixels(reader)

    assert (pixels.size_t, pixels.size_c, pixels.size_z, pixels.size_y, pixels.size_x) == (
        sizes["T"],
        sizes["C"],
        sizes["Z"],
        sizes["Y"],
        sizes["X"],
    )


@pytest.mark.parametrize("mode", _MODES)
def test_physical_sizes_equal_the_reader(tmp_path: Path, mode: str) -> None:
    fixture = write_synthetic_snouty(tmp_path)
    reader = SnoutyReader(fixture.dir, mode=mode)

    spacing = reader.physical_pixel_sizes
    pixels = _only_image_pixels(reader)

    assert (pixels.physical_size_x, pixels.physical_size_y, pixels.physical_size_z) == (
        spacing.X,
        spacing.Y,
        spacing.Z,
    )


def test_size_t_counts_the_files_not_the_sidecar(tmp_path: Path) -> None:
    """The scope validator forces the sidecar ``volumes_per_buffer`` to 1.

    One file per timepoint is the real time axis.
    """
    fixture = write_synthetic_snouty(tmp_path, n_timepoints=4)
    reader = SnoutyReader(fixture.dir)

    assert _only_image_pixels(reader).size_t == 4


def test_channels_carry_the_vendor_labels(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, channels=("LED", "488"))
    reader = SnoutyReader(fixture.dir)

    channels = _only_image_pixels(reader).channels

    assert [channel.name for channel in channels] == ["LED", "488"]
    assert len({channel.id for channel in channels}) == 2


def test_acquisition_date_comes_from_the_sidecar(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path)
    reader = SnoutyReader(fixture.dir)

    assert reader.ome_metadata.images[0].acquisition_date.isoformat() == SIDECAR_ACQUISITION_DATE


def test_a_sidecar_without_a_date_leaves_the_field_unset(tmp_path: Path) -> None:
    """The vendor always writes ``Date`` and ``Time``, but a truncated copy
    must degrade to a dateless Image rather than raise."""
    fixture = write_synthetic_snouty(tmp_path)
    sidecar = fixture.dir / "metadata" / "snap.txt"
    sidecar.write_text(
        "\n".join(line for line in sidecar.read_text().splitlines() if not line.startswith("Time:"))
    )
    reader = SnoutyReader(fixture.dir)

    assert reader.ome_metadata.images[0].acquisition_date is None


def test_no_objective_and_no_instrument(tmp_path: Path) -> None:
    """A Snouty sidecar records neither, so the OME must invent neither."""
    fixture = write_synthetic_snouty(tmp_path)
    reader = SnoutyReader(fixture.dir)

    ome = reader.ome_metadata
    assert ome.instruments == []
    assert ome.images[0].objective_settings is None


def test_each_position_reports_its_own_scene(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path, n_positions=3)
    reader = SnoutyReader(fixture.dir)

    for index, scene in enumerate(reader.scenes):
        reader.set_scene(index)
        assert reader.ome_metadata.images[0].name == scene


def test_plate_reader_reports_the_active_field(tmp_path: Path) -> None:
    """zarrmony reads ``images[0]`` and sets the scene first, so a plate
    reader that returned one Image per field would label every field zero."""
    fixture = write_synthetic_snouty(
        tmp_path,
        plate=PlateSpec(grammar="A", wells=((0, 0), (1, 1)), fields=((0, 0), (0, 1))),
    )
    reader = SnoutyPlateReader(fixture.dir)

    for index, scene in enumerate(reader.scenes):
        reader.set_scene(index)
        ome = reader.ome_metadata
        assert len(ome.images) == 1
        assert ome.images[0].name == scene


def test_session_reader_delegates_to_the_active_child(tmp_path: Path) -> None:
    session = tmp_path / "2026-07-14_10-12-21_ht_sols_gui"
    session.mkdir()
    write_synthetic_snouty(session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap")
    write_synthetic_snouty(
        session, subdir_name="2026-07-14_10-54-45_000_ht_sols_acquire", n_positions=2
    )
    reader = SnoutySessionReader(session)

    for index, scene in enumerate(reader.scenes):
        reader.set_scene(index)
        assert reader.ome_metadata.images[0].name == scene
