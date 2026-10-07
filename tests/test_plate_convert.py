"""End-to-end test: ``zarrmony convert`` on a synthetic plate.

This is the seam a user goes through, so it is the one that decides what the
plate store looks like on disk. The reader is reached through the entry
point, not imported, so a broken registration fails here too.
"""

from __future__ import annotations

from pathlib import Path

import zarr
import zarrmony

from tests.conftest import PlateSpec, assert_no_metadata_warnings, write_synthetic_snouty

# Two wells on the diagonal, two fields each. A01 and B02 are imaged; A02,
# A03, B01 and B03 are not.
DIAGONAL_PLATE = PlateSpec(grammar="A", wells=((0, 0), (1, 1)), fields=((0, 0), (0, 1)))


def test_convert_writes_one_hcs_plate_store(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path / "input", plate=DIAGONAL_PLATE)
    output = tmp_path / "plate.ome.zarr"

    audit = zarrmony.convert(fixture.dir, output)

    assert_no_metadata_warnings(audit)
    plate = zarr.open_group(str(output), mode="r").attrs["ome"]["plate"]
    assert plate["name"] == fixture.dir.name
    assert [row["name"] for row in plate["rows"]] == ["A", "B"]
    assert [column["name"] for column in plate["columns"]] == ["01", "02", "03"]
    assert [well["path"] for well in plate["wells"]] == ["A/01", "B/02"]
    assert plate["field_count"] == 2
    assert audit["layout"] == "plate"


def test_each_field_lands_in_its_well_group(tmp_path: Path) -> None:
    fixture = write_synthetic_snouty(tmp_path / "input", plate=DIAGONAL_PLATE)
    output = tmp_path / "plate.ome.zarr"

    assert_no_metadata_warnings(zarrmony.convert(fixture.dir, output))

    well = zarr.open_group(str(output / "A" / "01"), mode="r").attrs["ome"]["well"]
    assert [image["path"] for image in well["images"]] == ["0", "1"]
    for field_path in ("A/01/0", "A/01/1", "B/02/0", "B/02/1"):
        assert (output / field_path).is_dir(), f"{field_path} is missing from the store"


def test_unimaged_wells_get_no_group(tmp_path: Path) -> None:
    """Their row and column names are in the plate attrs all the same."""
    fixture = write_synthetic_snouty(tmp_path / "input", plate=DIAGONAL_PLATE)
    output = tmp_path / "plate.ome.zarr"

    assert_no_metadata_warnings(zarrmony.convert(fixture.dir, output))

    for unimaged in ("A/02", "A/03", "B/01", "B/03"):
        assert not (output / unimaged).exists(), f"{unimaged} was imaged by nobody"
    plate = zarr.open_group(str(output), mode="r").attrs["ome"]["plate"]
    assert len(plate["rows"]) == 2
    assert len(plate["columns"]) == 3
