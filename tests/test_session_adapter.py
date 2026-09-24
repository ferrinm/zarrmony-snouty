"""Tests for ``SnoutySessionReader`` — the top-level GUI-session directory
composer.

Covers the acceptance criteria for #5:

- Scene enumeration flat-concatenates every non-empty child's scenes
  (verbatim ``<subdir>[__pNNNNNN]`` names; no session-level prefix).
- Shallow-invalid subdirs are dropped with one
  ``SnoutySubdirSkippedWarning`` per skipped child.
- A session with zero surviving children raises ``SnoutyDataError``.
- ``set_scene`` swaps the active child and the child's active per-scene;
  ``xarray_dask_data``, ``physical_pixel_sizes``, ``channel_names``,
  ``metadata`` delegate to the active child.
- ``ZARRMONY_SNOUTY_MODE=desheared`` on the ``_open_session`` shim
  propagates to every child instantiated by the session reader.
- Session-level ``XY_stage_position_list.txt`` surfaces
  ``attrs.zarrmony.stage.xy_mm`` on multi-position children when the
  length matches, and emits ``SnoutySessionLayoutWarning`` (attrs
  omitted) when it doesn't.
- Both ``zarrmony-snouty`` and ``zarrmony-snouty-session`` register via
  the ``zarrmony.readers`` entry point.

A real-data smoke on ``/Volumes/kingsnout_lightsheet-ro/.../2026-07-14_10-12-21_ht_sols_gui/``
is opt-in via ``ZARRMONY_SNOUTY_REAL_SESSION_DIR`` (same pattern as the
per-subdir real-data smokes).
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import write_synthetic_snouty
from zarrmony_snouty import (
    SnoutySessionLayoutWarning,
    SnoutySessionReader,
    SnoutySubdirSkippedWarning,
    _open_session,
)
from zarrmony_snouty.adapter import SnoutyDataError, SnoutyModeError


def _make_session(root: Path, *, name: str = "2026-07-14_10-12-21_ht_sols_gui") -> Path:
    session = root / name
    session.mkdir()
    return session


def test_scenes_flat_concat_across_children(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    a = write_synthetic_snouty(session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap")
    b = write_synthetic_snouty(
        session, subdir_name="2026-07-14_10-54-45_000_ht_sols_acquire", n_positions=2
    )
    reader = SnoutySessionReader(session)
    assert reader.scenes == [
        a.dir.name,
        f"{b.dir.name}__p000000",
        f"{b.dir.name}__p000001",
    ]


def test_scene_names_are_verbatim_no_session_prefix(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    a = write_synthetic_snouty(session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap")
    reader = SnoutySessionReader(session)
    # Subdir names already carry the disambiguating info; adding a
    # session-level prefix would nest the timestamp inside itself.
    for scene in reader.scenes:
        assert session.name not in scene
    assert reader.scenes == [a.dir.name]


def test_children_sorted_deterministically(tmp_path: Path) -> None:
    # Create them out of alphabetical order to prove the reader sorts.
    session = _make_session(tmp_path)
    write_synthetic_snouty(session, subdir_name="2026-07-14_10-54-45_000_ht_sols_acquire")
    write_synthetic_snouty(session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap")
    reader = SnoutySessionReader(session)
    assert reader.scenes == [
        "2026-07-14_10-15-35_000_ht_sols_snap",
        "2026-07-14_10-54-45_000_ht_sols_acquire",
    ]


def test_skips_subdir_missing_data_dir_with_warning(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    good = write_synthetic_snouty(
        session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap"
    )
    bad = session / "2026-07-14_10-22-33_000_ht_sols_acquire"
    (bad / "metadata").mkdir(parents=True)
    (bad / "metadata" / "x.txt").write_text("k: v\n")

    with pytest.warns(SnoutySubdirSkippedWarning, match=r"missing_data_dir"):
        reader = SnoutySessionReader(session)
    assert reader.scenes == [good.dir.name]


def test_skips_subdir_missing_metadata_dir_with_warning(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    good = write_synthetic_snouty(
        session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap"
    )
    bad = session / "2026-07-14_10-22-33_000_ht_sols_acquire"
    (bad / "data").mkdir(parents=True)
    (bad / "data" / "x.tif").write_bytes(b"II*\x00")

    with pytest.warns(SnoutySubdirSkippedWarning, match=r"missing_metadata_dir"):
        reader = SnoutySessionReader(session)
    assert reader.scenes == [good.dir.name]


def test_skips_subdir_with_empty_data_with_warning(tmp_path: Path) -> None:
    # Mirrors the real-data case: 2026-07-14_10-22-33_000_ht_sols_acquire in
    # the /Volumes fixture has an empty data/ dir.
    session = _make_session(tmp_path)
    good = write_synthetic_snouty(
        session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap"
    )
    bad = session / "2026-07-14_10-22-33_000_ht_sols_acquire"
    (bad / "data").mkdir(parents=True)
    (bad / "metadata").mkdir()
    (bad / "metadata" / "x.txt").write_text("k: v\n")

    with pytest.warns(SnoutySubdirSkippedWarning, match=r"empty_data") as record:
        reader = SnoutySessionReader(session)
    assert reader.scenes == [good.dir.name]
    assert str(bad) in str(record[0].message)


def test_skips_subdir_with_no_metadata_txt_with_warning(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    good = write_synthetic_snouty(
        session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap"
    )
    bad = session / "2026-07-14_10-22-33_000_ht_sols_acquire"
    (bad / "data").mkdir(parents=True)
    (bad / "metadata").mkdir()
    (bad / "data" / "x.tif").write_bytes(b"II*\x00")
    (bad / "metadata" / "x.json").write_text("{}")

    with pytest.warns(SnoutySubdirSkippedWarning, match=r"no_metadata"):
        reader = SnoutySessionReader(session)
    assert reader.scenes == [good.dir.name]


def test_zero_surviving_children_raises_snouty_data_error(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    bad = session / "2026-07-14_10-22-33_000_ht_sols_acquire"
    (bad / "data").mkdir(parents=True)
    (bad / "metadata").mkdir()
    (bad / "metadata" / "x.txt").write_text("k: v\n")

    with pytest.warns(SnoutySubdirSkippedWarning):
        with pytest.raises(SnoutyDataError, match="no valid _ht_sols_"):
            SnoutySessionReader(session)


def test_zero_children_at_all_raises_snouty_data_error(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    with pytest.raises(SnoutyDataError):
        SnoutySessionReader(session)


def test_set_scene_forwards_to_correct_child_and_per_scene(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    a = write_synthetic_snouty(
        session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap"
    )
    b = write_synthetic_snouty(
        session,
        subdir_name="2026-07-14_10-54-45_000_ht_sols_acquire",
        n_positions=2,
    )
    reader = SnoutySessionReader(session)
    # scene 0 -> a[0]; scene 1 -> b[0]; scene 2 -> b[1]
    reader.set_scene(0)
    da0 = reader.xarray_dask_data.data.compute()
    # a is a single-position fixture, so plane values encode (z, t=0, p=0, c=0)
    for z in range(a.size_z):
        assert (da0[0, 0, z] == a.value_for(z)).all()

    reader.set_scene(1)
    da1 = reader.xarray_dask_data.data.compute()
    for z in range(b.size_z):
        assert (da1[0, 0, z] == b.value_for(z, t=0, p=0)).all()

    reader.set_scene(2)
    da2 = reader.xarray_dask_data.data.compute()
    for z in range(b.size_z):
        assert (da2[0, 0, z] == b.value_for(z, t=0, p=1)).all()


def test_set_scene_out_of_range_raises(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    write_synthetic_snouty(session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap")
    reader = SnoutySessionReader(session)
    with pytest.raises(IndexError):
        reader.set_scene(1)
    with pytest.raises(IndexError):
        reader.set_scene(-1)


def test_properties_delegate_to_active_child(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    a = write_synthetic_snouty(
        session,
        subdir_name="2026-07-14_10-15-35_000_ht_sols_snap",
        channels=("LED",),
        sample_px_um=0.1755,
    )
    b = write_synthetic_snouty(
        session,
        subdir_name="2026-07-14_10-16-14_000_ht_sols_acquire",
        channels=("488",),
        sample_px_um=0.2000,
    )
    reader = SnoutySessionReader(session)

    reader.set_scene(0)
    assert reader.channel_names == list(a.channels)
    assert reader.physical_pixel_sizes.X == pytest.approx(a.sample_px_um)
    assert reader.metadata == (a.dir / "metadata" / "snap.txt").read_text()

    reader.set_scene(1)
    assert reader.channel_names == list(b.channels)
    assert reader.physical_pixel_sizes.X == pytest.approx(b.sample_px_um)
    assert reader.metadata == (b.dir / "metadata" / "snap.txt").read_text()


def test_layout_hint_flat_and_plate_layout_none(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    write_synthetic_snouty(session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap")
    reader = SnoutySessionReader(session)
    assert reader.layout_hint == "flat"
    assert reader.plate_layout is None


def test_children_instantiated_lazily(tmp_path: Path) -> None:
    # __init__ must not invoke SnoutyReader on any child — the shallow
    # walk should be cheap enough that we can safely open a session with a
    # broken child and only pay the cost on set_scene into that child.
    session = _make_session(tmp_path)
    good = write_synthetic_snouty(
        session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap"
    )
    # A child whose sidecar sets volumes_per_buffer > 1 (unsupported). The
    # session reader must survive __init__ without raising — the error
    # only fires on set_scene(1).
    bad = write_synthetic_snouty(
        session, subdir_name="2026-07-14_10-16-14_000_ht_sols_acquire"
    )
    sidecar = bad.dir / "metadata" / "snap.txt"
    text = sidecar.read_text().replace(
        "volumes_per_buffer: 1", "volumes_per_buffer: 2"
    )
    sidecar.write_text(text)

    reader = SnoutySessionReader(session)  # must not raise
    assert reader.scenes == [good.dir.name, bad.dir.name]
    # Reading the good child is fine.
    reader.set_scene(0)
    _ = reader.xarray_dask_data
    # Committing to the bad child surfaces the error at that point.
    from zarrmony_snouty.adapter import SnoutyVolumesPerBufferUnsupportedError

    with pytest.raises(SnoutyVolumesPerBufferUnsupportedError):
        reader.set_scene(1)


def test_mode_propagates_to_every_child(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    a = write_synthetic_snouty(
        session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap"
    )
    b = write_synthetic_snouty(
        session, subdir_name="2026-07-14_10-16-14_000_ht_sols_acquire"
    )
    reader = SnoutySessionReader(session, mode="desheared")
    from zarrmony_snouty import _deshear

    reader.set_scene(0)
    max_shift = _deshear.max_deshear_shift(a.scan_step_size_px, a.size_z)
    assert reader.xarray_dask_data.shape == (1, 1, a.size_z, a.size_y + max_shift, a.size_x)

    reader.set_scene(1)
    max_shift = _deshear.max_deshear_shift(b.scan_step_size_px, b.size_z)
    assert reader.xarray_dask_data.shape == (1, 1, b.size_z, b.size_y + max_shift, b.size_x)


def test_unknown_mode_raises_snouty_mode_error(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    write_synthetic_snouty(session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap")
    with pytest.raises(SnoutyModeError, match="unknown SnoutyReader mode"):
        SnoutySessionReader(session, mode="sideways")


def test_open_session_reads_mode_env_var(tmp_path: Path, monkeypatch) -> None:
    session = _make_session(tmp_path)
    a = write_synthetic_snouty(
        session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap"
    )
    monkeypatch.setenv("ZARRMONY_SNOUTY_MODE", "desheared")
    reader = _open_session(session)
    from zarrmony_snouty import _deshear

    reader.set_scene(0)
    max_shift = _deshear.max_deshear_shift(a.scan_step_size_px, a.size_z)
    assert reader.xarray_dask_data.shape == (
        1,
        1,
        a.size_z,
        a.size_y + max_shift,
        a.size_x,
    )


def test_open_session_defaults_to_raw_when_env_unset(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ZARRMONY_SNOUTY_MODE", raising=False)
    session = _make_session(tmp_path)
    a = write_synthetic_snouty(
        session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap"
    )
    reader = _open_session(session)
    reader.set_scene(0)
    assert reader.xarray_dask_data.shape == (1, 1, a.size_z, a.size_y, a.size_x)


def test_open_session_rejects_unknown_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZARRMONY_SNOUTY_MODE", "sideways")
    session = _make_session(tmp_path)
    write_synthetic_snouty(session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap")
    with pytest.raises(SnoutyModeError):
        _open_session(session)


# ---------------------------------------------------------------------------
# XY position list (session-level) behavior


def test_xy_list_matching_length_surfaces_per_position_attrs(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    (session / "XY_stage_position_list.txt").write_text(
        "[0.0578, 0.0015],\n[0.1751, -0.028],\n"
    )
    b = write_synthetic_snouty(
        session,
        subdir_name="2026-07-14_10-54-45_000_ht_sols_acquire",
        n_positions=2,
    )
    reader = SnoutySessionReader(session)

    reader.set_scene(0)
    attrs0 = reader.xarray_dask_data.attrs
    assert attrs0 == {"zarrmony": {"stage": {"xy_mm": [0.0578, 0.0015]}}}

    reader.set_scene(1)
    attrs1 = reader.xarray_dask_data.attrs
    assert attrs1 == {"zarrmony": {"stage": {"xy_mm": [0.1751, -0.028]}}}

    # sanity: b was actually multi-position
    assert b.n_positions == 2


def test_xy_list_absent_omits_attrs_silently(tmp_path: Path, recwarn) -> None:
    session = _make_session(tmp_path)
    write_synthetic_snouty(
        session,
        subdir_name="2026-07-14_10-54-45_000_ht_sols_acquire",
        n_positions=2,
    )
    reader = SnoutySessionReader(session)
    reader.set_scene(0)
    assert reader.xarray_dask_data.attrs == {}
    assert not any(
        isinstance(w.message, SnoutySessionLayoutWarning) for w in recwarn.list
    )


def test_xy_list_mismatched_length_warns_and_omits_attrs(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    # List has 3 entries but the child has 2 positions — mismatched, session
    # reader must warn once per mismatched child and drop the attrs.
    (session / "XY_stage_position_list.txt").write_text(
        "[0.0, 0.0],\n[1.0, 0.0],\n[2.0, 0.0],\n"
    )
    write_synthetic_snouty(
        session,
        subdir_name="2026-07-14_10-54-45_000_ht_sols_acquire",
        n_positions=2,
    )
    with pytest.warns(SnoutySessionLayoutWarning, match=r"3 entries.*2 positions"):
        reader = SnoutySessionReader(session)
    reader.set_scene(0)
    assert reader.xarray_dask_data.attrs == {}
    reader.set_scene(1)
    assert reader.xarray_dask_data.attrs == {}


def test_xy_list_ignored_for_single_position_child(tmp_path: Path, recwarn) -> None:
    # Single-position children never look up XY (they have no _p in
    # filenames). Session reader must not warn when the list length differs
    # from these — the mismatch only matters for multi-position children.
    session = _make_session(tmp_path)
    (session / "XY_stage_position_list.txt").write_text("[0.0, 0.0],\n[1.0, 0.0],\n")
    write_synthetic_snouty(session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap")
    reader = SnoutySessionReader(session)
    reader.set_scene(0)
    assert reader.xarray_dask_data.attrs == {}
    assert not any(
        isinstance(w.message, SnoutySessionLayoutWarning) for w in recwarn.list
    )


# ---------------------------------------------------------------------------
# Plugin registration smoke


def test_both_plugins_register_via_entry_point() -> None:
    from zarrmony.readers.plugin import list_plugins

    names = {p.name for p in list_plugins()}
    assert "zarrmony-snouty" in names
    assert "zarrmony-snouty-session" in names


def test_session_plugin_carries_expected_provenance() -> None:
    from zarrmony.readers.plugin import list_plugins

    plugins = {p.name: p for p in list_plugins()}
    p = plugins["zarrmony-snouty-session"]
    assert p.distribution == "zarrmony-snouty"
    assert p.source == "entry_point"


# ---------------------------------------------------------------------------
# Real-data smoke — opt-in via env var, same pattern as the per-subdir smokes.

REAL_SESSION_ENV_VAR = "ZARRMONY_SNOUTY_REAL_SESSION_DIR"


def test_real_session_smoke() -> None:
    """Smoke test on a real ``*_ht_sols_gui/`` GUI-session directory.

    Point ``ZARRMONY_SNOUTY_REAL_SESSION_DIR`` at any Snouty GUI-session
    directory (e.g. ``/Volumes/kingsnout_lightsheet-ro/.../2026-07-14_10-12-21_ht_sols_gui/``).
    Confirms that the session reader enumerates one scene per non-empty
    ``_ht_sols_*`` subdir with ``__pNNNNNN`` suffixes on multi-position
    children, and that materializing the first timepoint of every scene
    produces a real uint16 array. Skipped when unset so CI and forks stay
    clean; kept out of the tree because real acquisition paths embed
    colleague names and sample IDs.
    """
    env_path = os.environ.get(REAL_SESSION_ENV_VAR)
    if not env_path:
        pytest.skip(f"{REAL_SESSION_ENV_VAR} not set; skipping real-data smoke")
    real_dir = Path(env_path)
    if not real_dir.is_dir():
        pytest.skip(f"{REAL_SESSION_ENV_VAR}={env_path} is not a directory; skipping")

    # Some children in the real fixture (e.g. 2026-07-14_10-22-33_000_ht_sols_acquire)
    # have empty data/ dirs — the shallow validator drops them with a warning.
    with pytest.warns(SnoutySubdirSkippedWarning):
        reader = SnoutySessionReader(real_dir)
    assert len(reader.scenes) > 0

    # Materialize the first timepoint of every scene. vpb>1 subdirs (a
    # known-unsupported guardrail — see #1's residual note in CHANGELOG)
    # raise SnoutyVolumesPerBufferUnsupportedError; that's expected error
    # propagation, not a session-reader bug, so we count it separately and
    # only assert that at least one scene materializes successfully.
    from zarrmony_snouty.adapter import SnoutyVolumesPerBufferUnsupportedError

    ok = 0
    vpb_skipped = 0
    for i in range(len(reader.scenes)):
        try:
            reader.set_scene(i)
            xr_da = reader.xarray_dask_data
        except SnoutyVolumesPerBufferUnsupportedError:
            vpb_skipped += 1
            continue
        assert xr_da.dims == ("T", "C", "Z", "Y", "X")
        first = xr_da.isel(T=0).data.compute()
        assert first.dtype == np.uint16
        assert first.any(), f"scene {reader.scenes[i]!r} materialized as all zeros"
        ok += 1
    assert ok > 0, (
        f"no scenes materialized (vpb_skipped={vpb_skipped}); "
        "session reader is not composing correctly"
    )
