"""End-to-end test: ``zarrmony convert`` on a synthetic flat acquisition.

The flat shape is what most of this plugin's users convert, and until #39
nothing here called ``zarrmony.convert`` on it. Only the plate path reached a
writer, so a reader surface that every convert reads could go missing and the
suite stayed green. This module closes that gap.

A session convert goes through the same per-scene writer, one store per
child, so it is covered here rather than in its own module.

The reader is reached through the entry point, not imported, so a broken
registration fails here too.
"""

from __future__ import annotations

from pathlib import Path

import zarrmony

from tests.conftest import (
    SIDECAR_ACQUISITION_DATE,
    assert_no_metadata_warnings,
    write_synthetic_snouty,
)


def _convert(tmp_path: Path) -> dict:
    fixture = write_synthetic_snouty(tmp_path / "input")
    return zarrmony.convert(fixture.dir, tmp_path / "flat.ome.zarr")


def _only_scene(audit: dict) -> dict:
    stores = audit["stores"]
    assert len(stores) == 1
    scenes = stores[0]["per_scene"]
    assert len(scenes) == 1
    return scenes[0]


def test_convert_records_no_metadata_warning(tmp_path: Path) -> None:
    """The ``ExtractorWarning`` itself is an error — see the pytest gate."""
    assert_no_metadata_warnings(_convert(tmp_path))


def test_audit_records_the_channel_identities(tmp_path: Path) -> None:
    scene = _only_scene(_convert(tmp_path))

    assert [channel["name"] for channel in scene["channels"]] == ["LED"]


def test_audit_records_the_sidecar_acquisition_date(tmp_path: Path) -> None:
    scene = _only_scene(_convert(tmp_path))

    assert scene["acquisition"]["date"] == SIDECAR_ACQUISITION_DATE


def test_audit_keeps_the_reader_hook_contributions(tmp_path: Path) -> None:
    """The OME object must not displace what ``acquisition_audit`` already fills."""
    acquisition = _only_scene(_convert(tmp_path))["acquisition"]

    assert acquisition["imaging_method"] == ["light_sheet"]
    assert acquisition["microscope"] == "HT-SOLS"
    assert acquisition["zarrmony_snouty"]["engine_used"] == "cpu"


def test_audit_records_no_objective(tmp_path: Path) -> None:
    """A Snouty sidecar records no objective, so the audit must invent none."""
    assert _only_scene(_convert(tmp_path)).get("objective") is None


def test_converting_a_session_writes_one_clean_store_per_child(tmp_path: Path) -> None:
    """Each child store carries its own scene name, channels and date."""
    session = tmp_path / "2026-07-14_10-12-21_ht_sols_gui"
    session.mkdir()
    write_synthetic_snouty(session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap")
    write_synthetic_snouty(session, subdir_name="2026-07-14_10-54-45_000_ht_sols_acquire")

    audit = zarrmony.convert(session, tmp_path / "session.ome.zarr")

    assert_no_metadata_warnings(audit)
    scenes = [store["per_scene"][0] for store in audit["stores"]]
    assert [scene["scene_name"] for scene in scenes] == [
        "2026-07-14_10-15-35_000_ht_sols_snap",
        "2026-07-14_10-54-45_000_ht_sols_acquire",
    ]
    for scene in scenes:
        assert [channel["name"] for channel in scene["channels"]] == ["LED"]
        assert scene["acquisition"]["date"] == SIDECAR_ACQUISITION_DATE
