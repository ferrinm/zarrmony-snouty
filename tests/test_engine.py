"""Engine selection (issue #10).

*Mode* is the output geometry. *Engine* is where it computes. Only
``traditional`` has a GPU path (ADR-0002, decision 2).

These tests run on any host. The three facts that need hardware — whether
cupy imported, how many bytes the device has free, and the device transform
itself — are each one substitutable leaf in
:mod:`zarrmony_snouty._deshear_gpu`, and :func:`fake_device` replaces them.
The resolution logic under test is the same code a GPU host runs.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tests.conftest import write_synthetic_snouty
from zarrmony_snouty import _deshear, _deshear_gpu, _open, _open_session
from zarrmony_snouty._errors import SnoutyEngineError
from zarrmony_snouty._metadata import SnoutyMetadataError
from zarrmony_snouty.adapter import SnoutyReader
from zarrmony_snouty.session import SnoutySessionReader


@pytest.fixture
def fake_device(monkeypatch):
    """Substitute the leaves of :mod:`_deshear_gpu` that need real hardware.

    ``cupy_available`` answers whether the soft import succeeded, and
    ``free_device_bytes`` answers how much room the card has. Nothing else in
    the resolution path touches a device, so fixing these two makes every
    branch of the decision table reachable from a CPU-only host.
    """

    def configure(*, cupy_available: bool, free_bytes: int | None) -> None:
        monkeypatch.setattr(_deshear_gpu, "cupy_available", cupy_available)
        monkeypatch.setattr(_deshear_gpu, "free_device_bytes", lambda: free_bytes)

    return configure


def test_unknown_engine_is_rejected(synthetic_snouty) -> None:
    """Mirrors the unknown-mode guard. A typo must not silently become CPU."""
    with pytest.raises(SnoutyEngineError, match="unknown SnoutyReader engine"):
        SnoutyReader(synthetic_snouty.dir, engine="turbo")


def test_auto_falls_back_to_cpu_without_cupy(synthetic_snouty, fake_device) -> None:
    """Both attributes are set during construction, so a caller can read the
    decision before it computes a single voxel."""
    fake_device(cupy_available=False, free_bytes=None)
    reader = SnoutyReader(synthetic_snouty.dir, mode="traditional", engine="auto")
    assert reader.engine_used == "cpu"
    assert reader.engine_fallback_reason == "cupy not installed"


def test_explicit_cpu_bypasses_the_device_entirely(synthetic_snouty, fake_device) -> None:
    """A caller who asked for the CPU gave nothing up, so there is no reason
    to report. This is the v0.2 path, and a working card must not divert it."""
    fake_device(cupy_available=True, free_bytes=10**12)
    reader = SnoutyReader(synthetic_snouty.dir, mode="traditional", engine="cpu")
    assert reader.engine_used == "cpu"
    assert reader.engine_fallback_reason is None


def test_explicit_gpu_raises_without_cupy(synthetic_snouty, fake_device) -> None:
    """A missing cupy means the environment is wrong, and a person must fix
    it. Converting on the CPU instead would write different pixels than the
    caller asked for, and say so only in an attribute nobody reads."""
    fake_device(cupy_available=False, free_bytes=None)
    with pytest.raises(SnoutyEngineError, match="cupy"):
        SnoutyReader(synthetic_snouty.dir, mode="traditional", engine="gpu")


def test_auto_falls_back_to_cpu_without_a_device(synthetic_snouty, fake_device) -> None:
    """cupy imports on a host with no card, a stale driver, or a card in a bad
    state. ``free_device_bytes`` reports ``None`` for all three, and all three
    mean the same thing to the resolver: do not plan on the GPU."""
    fake_device(cupy_available=True, free_bytes=None)
    reader = SnoutyReader(synthetic_snouty.dir, mode="traditional", engine="auto")
    assert reader.engine_used == "cpu"
    assert reader.engine_fallback_reason == "no CUDA device visible"


def test_explicit_gpu_raises_without_a_device(synthetic_snouty, fake_device) -> None:
    """Second of the two wrong-environment conditions. Same rule as a missing
    cupy: raise rather than quietly write CPU pixels."""
    fake_device(cupy_available=True, free_bytes=None)
    with pytest.raises(SnoutyEngineError, match="no CUDA device visible"):
        SnoutyReader(synthetic_snouty.dir, mode="traditional", engine="gpu")


@pytest.mark.parametrize("mode", ["raw", "desheared"])
def test_gpu_on_a_mode_without_a_gpu_path_is_not_an_error(
    synthetic_snouty, fake_device, mode
) -> None:
    """ADR-0002, decision 2: only ``traditional`` has a GPU path.

    The mode check runs before the environment checks, so the answer does not
    depend on the host. cupy is absent here, and the reader still reports the
    by-design reason rather than complaining about the install — a missing
    cupy is irrelevant to a mode that would never have used it.
    """
    fake_device(cupy_available=False, free_bytes=None)
    reader = SnoutyReader(synthetic_snouty.dir, mode=mode, engine="gpu")
    assert reader.engine_used == "cpu"
    assert reader.engine_fallback_reason == f"mode {mode!r} has no GPU path"


# What the synthetic fixture's traditional transform needs on the device:
# the desheared intermediate plus the rotated output, one channel, uint16.
# Derived by hand from the shape formulas that _deshear documents, not read
# back from the transform, so the figure is an independent expectation.
#   desheared (4, 6 + round(7 * 3), 16)      =  1728 voxels
#   rotated   (6, 49, 16)                    =  4704 voxels
#   (1728 + 4704) * 2 bytes                  = 12864 bytes
FIXTURE_DEVICE_BYTES = 12864


def test_auto_falls_back_to_cpu_when_the_scene_does_not_fit(synthetic_snouty, fake_device) -> None:
    """The reason names both figures, so a person reading it can tell a card
    that is slightly too small from one that is nowhere near."""
    fake_device(cupy_available=True, free_bytes=4000)
    reader = SnoutyReader(synthetic_snouty.dir, mode="traditional", engine="auto")
    assert reader.engine_used == "cpu"
    assert reader.engine_fallback_reason == "device memory 4.00 kB below required 12.86 kB"


def test_explicit_gpu_runs_an_oversized_scene_on_the_cpu(synthetic_snouty, fake_device) -> None:
    """Capacity is not a broken environment, so this is the one GPU-request
    failure that does not raise. One scene being too large for the card is a
    worse reason to abandon a long convert than it is to shift that scene."""
    fake_device(cupy_available=True, free_bytes=4000)
    reader = SnoutyReader(synthetic_snouty.dir, mode="traditional", engine="gpu")
    assert reader.engine_used == "cpu"
    assert reader.engine_fallback_reason == "device memory 4.00 kB below required 12.86 kB"


def test_auto_picks_the_gpu_when_the_mode_host_and_card_all_allow_it(
    synthetic_snouty, fake_device
) -> None:
    fake_device(cupy_available=True, free_bytes=10**12)
    reader = SnoutyReader(synthetic_snouty.dir, mode="traditional", engine="auto")
    assert reader.engine_used == "gpu"
    assert reader.engine_fallback_reason is None


@pytest.fixture
def device_probe(monkeypatch):
    """Record every call that reaches the device, and answer without one.

    ``_rotate_on_device`` is the single leaf in the GPU path that touches
    hardware. Substituting it lets a CPU-only host prove *that* the pixels
    were routed to the GPU module, which is a different claim from what the
    GPU computes — ``tests/test_deshear_gpu.py`` covers that on real hardware.

    ``_rotate_on_device`` owns the Y/Z swap, so the probe returns the swapped
    shape, which is what :func:`_deshear.traditional_shape` advertises.
    """
    calls: list[tuple[int, int, int]] = []

    def probe(desheared, **kwargs):
        shape = kwargs["output_shape"]
        calls.append(shape)
        return np.zeros((shape[1], shape[0], shape[2]), dtype=desheared.dtype)

    monkeypatch.setattr(_deshear_gpu, "_rotate_on_device", probe)
    return calls


def test_gpu_engine_sends_the_transform_to_the_gpu_module(
    synthetic_snouty, fake_device, device_probe
) -> None:
    """Resolution alone proves nothing. This asserts that the resolved engine
    actually changes which code transforms the pixels."""
    fake_device(cupy_available=True, free_bytes=10**12)
    reader = SnoutyReader(synthetic_snouty.dir, mode="traditional", engine="gpu")
    assert reader.engine_used == "gpu"

    data = reader.xarray_dask_data.compute()

    assert device_probe, "no timepoint reached the device"
    assert data.shape == (
        1,
        1,
        *_deshear.traditional_shape(
            synthetic_snouty.size_z,
            synthetic_snouty.size_y,
            synthetic_snouty.size_x,
            synthetic_snouty.scan_step_size_px,
            synthetic_snouty.voxel_aspect_ratio,
        ),
    )


def test_cpu_engine_never_reaches_the_device(synthetic_snouty, fake_device, device_probe) -> None:
    """The other half of the dispatch claim. A card that is present and ample
    must not pull a ``engine="cpu"`` convert onto the GPU."""
    fake_device(cupy_available=True, free_bytes=10**12)
    reader = SnoutyReader(synthetic_snouty.dir, mode="traditional", engine="cpu")

    reader.xarray_dask_data.compute()

    assert device_probe == []


# A session with two children of different geometry. The small one fits the
# stubbed card and the large one does not. Required device bytes per child,
# derived by hand from the shape formulas exactly as FIXTURE_DEVICE_BYTES was:
#   size_z=4   ->  12864 bytes  (12.86 kB)
#   size_z=12  ->  60096 bytes  (60.10 kB)
SMALL_CHILD = "2026-07-14_10-15-35_000_ht_sols_snap"
LARGE_CHILD = "2026-07-14_10-54-45_001_ht_sols_snap"
BETWEEN_THE_TWO = 30000


def _two_child_session(root: Path) -> Path:
    session = root / "2026-07-14_10-12-21_ht_sols_gui"
    session.mkdir()
    write_synthetic_snouty(session, subdir_name=SMALL_CHILD)
    write_synthetic_snouty(session, subdir_name=LARGE_CHILD, size_z=12)
    return session


def _engine_per_scene(reader) -> list[tuple[str, str | None]]:
    seen = []
    for index in range(len(reader.scenes)):
        reader.set_scene(index)
        seen.append((reader.engine_used, reader.engine_fallback_reason))
    return seen


def test_session_auto_resolves_one_engine_for_every_scene(tmp_path, fake_device) -> None:
    """All-or-nothing. The small child fits the card on its own, and still
    runs on the CPU, because one output store must never mix engines — the
    two engines do not produce identical pixels (#18).

    Every scene carries the same reason, naming the child that did not fit.
    """
    session = _two_child_session(tmp_path)
    fake_device(cupy_available=True, free_bytes=BETWEEN_THE_TWO)

    reader = SnoutySessionReader(session, mode="traditional", engine="auto")

    assert _engine_per_scene(reader) == [
        ("cpu", "device memory 30.00 kB below required 60.10 kB"),
        ("cpu", "device memory 30.00 kB below required 60.10 kB"),
    ]


def test_session_explicit_gpu_shifts_only_the_scene_that_does_not_fit(
    tmp_path, fake_device
) -> None:
    """The counterpart to all-or-nothing, and the difference is consent.

    A caller who named the engine accepted the mix. The small child keeps the
    card, the large one does not fit and moves to the CPU, and only the moved
    one carries a reason.
    """
    session = _two_child_session(tmp_path)
    fake_device(cupy_available=True, free_bytes=BETWEEN_THE_TWO)

    reader = SnoutySessionReader(session, mode="traditional", engine="gpu")

    assert _engine_per_scene(reader) == [
        ("gpu", None),
        ("cpu", "device memory 30.00 kB below required 60.10 kB"),
    ]


# ``ReaderPlugin.open`` takes only a path, so the engine is opted in through
# an env var, exactly as the mode already is. These test the two plugin shims.


@pytest.fixture
def traditional_env(monkeypatch):
    """Put the shims in the one mode that has a GPU path, engine unset."""
    monkeypatch.setenv("ZARRMONY_SNOUTY_MODE", "traditional")
    monkeypatch.delenv("ZARRMONY_SNOUTY_ENGINE", raising=False)


def test_open_defaults_to_auto_when_the_engine_env_is_unset(
    synthetic_snouty, traditional_env, fake_device
) -> None:
    """The default must be ``auto``, not ``cpu``. The two differ in what they
    report: ``auto`` names what it gave up, and ``cpu`` gave up nothing."""
    fake_device(cupy_available=False, free_bytes=None)
    reader = _open(synthetic_snouty.dir)
    assert reader.engine_used == "cpu"
    assert reader.engine_fallback_reason == "cupy not installed"


def test_open_reads_the_engine_from_the_env(
    synthetic_snouty, traditional_env, fake_device, monkeypatch
) -> None:
    """A GPU request through the env var is as strict as one through the
    kwarg. A wrong environment raises rather than converting on the CPU."""
    monkeypatch.setenv("ZARRMONY_SNOUTY_ENGINE", "gpu")
    fake_device(cupy_available=False, free_bytes=None)
    with pytest.raises(SnoutyEngineError, match="cupy"):
        _open(synthetic_snouty.dir)


def test_open_rejects_an_unknown_engine_env_value(
    synthetic_snouty, traditional_env, monkeypatch
) -> None:
    """A typo in a batch script must stop the convert, not silently pick an
    engine for the caller."""
    monkeypatch.setenv("ZARRMONY_SNOUTY_ENGINE", "turbo")
    with pytest.raises(SnoutyEngineError, match="unknown SnoutyReader engine"):
        _open(synthetic_snouty.dir)


def test_open_session_reads_the_engine_from_the_env(
    tmp_path, traditional_env, fake_device, monkeypatch
) -> None:
    """One env var covers every child in the batch, same contract as the mode
    var. ``cpu`` here, because an ample card would otherwise give ``gpu``."""
    session = _two_child_session(tmp_path)
    monkeypatch.setenv("ZARRMONY_SNOUTY_ENGINE", "cpu")
    fake_device(cupy_available=True, free_bytes=10**12)

    reader = _open_session(session)

    assert _engine_per_scene(reader) == [("cpu", None), ("cpu", None)]


# The engine decision is provenance: months later, the only way to explain
# why two stores from one session differ is a record of which engine wrote
# each one. It goes under a ``zarrmony_snouty`` key so it cannot collide with
# zarrmony's own documented audit vocabulary.


def test_audit_records_a_fallback_and_the_reason_for_it(synthetic_snouty, fake_device) -> None:
    fake_device(cupy_available=False, free_bytes=None)
    reader = SnoutyReader(synthetic_snouty.dir, mode="traditional", engine="auto")
    assert reader.acquisition_audit["zarrmony_snouty"] == {
        "engine_used": "cpu",
        "engine_fallback_reason": "cupy not installed",
        "cupy_version": None,
        "cuda_runtime_version": None,
    }


def test_audit_records_the_device_library_versions(
    synthetic_snouty, fake_device, monkeypatch
) -> None:
    """The versions describe the environment, not the outcome, so a reader
    can tell a cupy upgrade apart from a hardware change as the cause of a
    pixel difference between two converts."""
    fake_device(cupy_available=True, free_bytes=10**12)
    monkeypatch.setattr(_deshear_gpu, "cupy_version", lambda: "13.4.1")
    monkeypatch.setattr(_deshear_gpu, "cuda_runtime_version", lambda: "12.4")

    reader = SnoutyReader(synthetic_snouty.dir, mode="traditional", engine="gpu")

    assert reader.acquisition_audit["zarrmony_snouty"] == {
        "engine_used": "gpu",
        "engine_fallback_reason": None,
        "cupy_version": "13.4.1",
        "cuda_runtime_version": "12.4",
    }


def test_audit_keeps_the_static_acquisition_fields(synthetic_snouty) -> None:
    """The namespaced sub-dict is an addition. zarrmony composes the audit
    with ``setdefault``, so losing either static key would silently drop a
    field from every converted store."""
    audit = SnoutyReader(synthetic_snouty.dir).acquisition_audit
    assert audit["imaging_method"] == ["light_sheet"]
    assert audit["microscope"] == "HT-SOLS"


def test_session_audit_reports_each_scene_own_engine(tmp_path, fake_device) -> None:
    """One session, two stores, two engines, and the audit tells them apart.
    Without this the mixed convert that ``engine="gpu"`` permits would be
    unexplainable after the fact."""
    session = _two_child_session(tmp_path)
    fake_device(cupy_available=True, free_bytes=BETWEEN_THE_TWO)

    reader = SnoutySessionReader(session, mode="traditional", engine="gpu")

    seen = []
    for index in range(len(reader.scenes)):
        reader.set_scene(index)
        seen.append(reader.acquisition_audit["zarrmony_snouty"]["engine_used"])
    assert seen == ["gpu", "cpu"]


def test_a_child_with_an_unreadable_sidecar_does_not_abort_the_session(
    tmp_path, fake_device
) -> None:
    """Sizing the card reads every child's sidecar, and one bad sidecar must
    not take the batch down at construction.

    The session reader promises that a broken child costs its own scene and
    nothing else. Engine resolution has to keep that promise, so a child it
    cannot measure drops out of the sizing and raises later, on the
    ``set_scene`` that commits to it.
    """
    session = _two_child_session(tmp_path)
    sidecar = session / LARGE_CHILD / "metadata" / "snap.txt"
    sidecar.write_text(
        "\n".join(
            line
            for line in sidecar.read_text().splitlines()
            if not line.startswith("voxel_aspect_ratio")
        )
    )
    fake_device(cupy_available=True, free_bytes=10**12)

    reader = SnoutySessionReader(session, mode="traditional", engine="auto")

    reader.set_scene(0)
    assert reader.engine_used == "gpu"
    with pytest.raises(SnoutyMetadataError):
        reader.set_scene(1)


@pytest.mark.parametrize("mode", ["raw", "desheared", "traditional"])
def test_the_default_engine_writes_the_v0_2_pixels_on_a_cpu_only_host(
    synthetic_snouty, fake_device, mode
) -> None:
    """The regression guard for the whole feature.

    ``engine`` defaults to ``auto``, and on a host with no cupy that must
    produce exactly what ``engine="cpu"`` produces. Byte equality, not
    approximate: a CPU-only install of v0.3 must write the same stores as
    v0.2 did. Every mode, because the resolver runs for all three.
    """
    fake_device(cupy_available=False, free_bytes=None)
    default = SnoutyReader(synthetic_snouty.dir, mode=mode)
    explicit = SnoutyReader(synthetic_snouty.dir, mode=mode, engine="cpu")

    np.testing.assert_array_equal(
        default.xarray_dask_data.compute().to_numpy(),
        explicit.xarray_dask_data.compute().to_numpy(),
    )
