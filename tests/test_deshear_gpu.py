"""GPU ``traditional`` transform (issue #8).

Expected device footprints come from ADR-0002's measured table for
acquisition A, not from re-running the formula the implementation uses.
Acquisition A is ``slices_per_volume: 411``, ``height_px: 600`` (592 after the
timestamp-strip crop), ``width_px: 1500``, ``scan_step_size_px: 2``,
``voxel_aspect_ratio: 2.856``, one channel, uint16.

The device footprint is deliberately smaller than the host footprint
``_hostmem`` computes. Deshear stays on the CPU (ADR-0002, decision 2), so the
raw input volume is never uploaded.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from zarrmony_snouty import _deshear, _deshear_gpu
from zarrmony_snouty._errors import SnoutyEngineError

# Acquisition A geometry, from ADR-0002 "Measurements".
ACQ_A = {
    "size_z": 411,
    "size_y": 592,
    "size_x": 1500,
    "scan_step_size_px": 2.0,
    "voxel_aspect_ratio": 2.856,
    "itemsize": 2,
}

GB = 1000**3


def test_device_requirement_is_the_intermediate_plus_the_output():
    """ADR-0002: desheared intermediate 1.74 GB + rotated output 2.43 GB.

    The raw input volume (0.73 GB) is absent on purpose. Deshear runs on the
    CPU, so only the desheared array crosses the PCIe bus.
    """
    got = _deshear_gpu.required_device_bytes(**ACQ_A)
    assert got / GB == pytest.approx(4.17, abs=0.01)


def test_module_imports_without_cupy_and_reports_availability():
    """The soft import is the whole point: a CPU-only install must still be
    able to ``import zarrmony_snouty._deshear_gpu`` and ask whether cupy is
    there. The test does its own import attempt rather than trusting the
    module's answer about itself."""
    try:
        import cupy  # noqa: F401
    except ImportError:
        expected = False
    else:
        expected = True

    assert _deshear_gpu.cupy_available is expected


@pytest.mark.skipif(_deshear_gpu.cupy_available, reason="cupy is installed")
def test_free_device_bytes_is_none_without_cupy():
    """``None`` means "unknowable", which is what a caller needs in order to
    tell "no device" apart from "a device with zero bytes free"."""
    assert _deshear_gpu.free_device_bytes() is None


@pytest.mark.gpu
@pytest.mark.skipif(not _deshear_gpu.cupy_available, reason="cupy is not installed")
def test_free_device_bytes_is_positive_on_a_real_device():
    free = _deshear_gpu.free_device_bytes()
    assert isinstance(free, int)
    assert free > 0


@pytest.mark.skipif(_deshear_gpu.cupy_available, reason="cupy is installed")
def test_transform_raises_without_cupy():
    """Never fall back to the CPU silently. The caller asked for the GPU
    path, and :mod:`zarrmony_snouty._deshear` is one import away if it wants
    the CPU one."""
    volume = np.zeros((4, 5, 6), dtype=np.uint16)
    with pytest.raises(SnoutyEngineError, match="cupy"):
        _deshear_gpu.traditional_zyx(volume, 2.0, 2.856)


def test_device_work_is_serialized(monkeypatch):
    """ADR-0002, decision 3. Dask runs one thread per core, and each
    ``traditional`` call holds 4.17 GB on the device. Without the lock, 16
    threads demand 67 GB from a 23.46 GiB card.

    Substitutes the one leaf that needs hardware, so the property is checked
    on any host. The CPU deshear runs unlocked and is not counted.
    """
    hold = 0.02
    n_threads = 8
    live = 0
    peak = 0
    calls = 0
    guard = threading.Lock()

    def probe(desheared, **kwargs):
        nonlocal live, peak, calls
        with guard:
            live += 1
            peak = max(peak, live)
            calls += 1
        time.sleep(hold)
        with guard:
            live -= 1
        return np.zeros(kwargs["output_shape"], dtype=desheared.dtype)

    monkeypatch.setattr(_deshear_gpu, "_rotate_on_device", probe)
    volume = np.zeros((4, 5, 6), dtype=np.uint16)

    started = time.monotonic()
    threads = [
        threading.Thread(target=_deshear_gpu.traditional_zyx, args=(volume, 2.0, 2.856))
        for _ in range(n_threads)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.monotonic() - started

    assert calls == n_threads, "every thread must reach the device leaf"
    assert peak == 1, f"{peak} threads were on the device at once"
    # Serialization forces the holds to add up rather than overlap. A margin
    # covers sleep imprecision without admitting two-way overlap.
    assert elapsed >= 0.8 * n_threads * hold


# Acquisition A with X cut to 64. X is geometrically inert under this
# transform — the source coordinate for the X axis is the output index
# itself — so the rotated grid, and therefore every differing line, is
# identical to the full-width acquisition. The fixture runs in about two
# seconds instead of forty.
FIXTURE_Z, FIXTURE_Y, FIXTURE_X = 411, 592, 64
STEP = ACQ_A["scan_step_size_px"]
ASPECT = ACQ_A["voxel_aspect_ratio"]


def _fixture_volume() -> np.ndarray:
    """Uniform noise with no zeros, so a fill value is distinguishable from
    a real voxel that happens to be dark."""
    rng = np.random.default_rng(0)
    return rng.integers(1, 4096, size=(FIXTURE_Z, FIXTURE_Y, FIXTURE_X), dtype=np.uint16)


def _distance_from_half_integer(coord: np.ndarray) -> np.ndarray:
    return np.abs((coord - np.floor(coord)) - 0.5)


@pytest.mark.gpu
@pytest.mark.skipif(not _deshear_gpu.cupy_available, reason="cupy is not installed")
def test_gpu_output_has_the_predicted_shape():
    volume = np.zeros((16, 20, 8), dtype=np.uint16)
    got = _deshear_gpu.traditional_zyx(volume, STEP, ASPECT)
    assert got.shape == _deshear.traditional_shape(16, 20, 8, STEP, ASPECT)
    assert got.dtype == volume.dtype


@pytest.mark.gpu
@pytest.mark.skipif(not _deshear_gpu.cupy_available, reason="cupy is not installed")
def test_gpu_agrees_with_the_cpu_reference_within_the_measured_bound():
    """Byte-identity is not achievable, and #18 measured why.

    ``cupyx.scipy.ndimage`` and ``scipy.ndimage`` break nearest-neighbour
    ties differently at ``order=0``, and they disagree about whether a source
    coordinate of exactly ``0.0`` is in bounds. Both effects are confined to
    lines the geometry predicts, so the test asserts location, not equality.

    The predicted locations are computed here from the affine matrix, not
    read back from anything the transform produced. Asserting against the
    implementation's own output would pass by construction.
    """
    volume = _fixture_volume()
    cpu = _deshear.traditional_zyx(volume, STEP, ASPECT)
    gpu = _deshear_gpu.traditional_zyx(volume, STEP, ASPECT)
    assert gpu.shape == cpu.shape

    differing = cpu != gpu
    share = int(differing.sum()) / differing.size
    assert share < 1e-4, f"{share:.5%} of voxels differ, bound is 0.01%"

    # Each differing voxel belongs to one (a, b) line of the rotated grid.
    # traditional_zyx swaps the first two axes then flips the first, so
    # output index (p, q) came from affine-output index (a=q, b=n_p-1-p).
    n_p = cpu.shape[0]
    p, q, _x = np.nonzero(differing)
    lines = np.unique(np.stack([q, (n_p - 1) - p], axis=1), axis=0)

    matrix = np.linalg.inv(_deshear._affine_matrix(STEP / ASPECT, ASPECT))
    a = lines[:, 0].astype(np.float64)
    b = lines[:, 1].astype(np.float64)
    coord_0 = matrix[0, 0] * a + matrix[0, 1] * b
    coord_1 = matrix[1, 0] * a + matrix[1, 1] * b

    is_tie = (
        np.minimum(
            _distance_from_half_integer(coord_0),
            _distance_from_half_integer(coord_1),
        )
        < 1e-4
    )

    limit_0, limit_1, _ = _deshear.desheared_shape(FIXTURE_Z, FIXTURE_Y, FIXTURE_X, STEP)
    edges = {
        "axis0 low": np.abs(coord_0) < 1e-9,
        "axis0 high": np.abs(coord_0 - (limit_0 - 1)) < 1e-9,
        "axis1 low": np.abs(coord_1) < 1e-9,
        "axis1 high": np.abs(coord_1 - (limit_1 - 1)) < 1e-9,
    }
    at_edge = np.logical_or.reduce(list(edges.values()))

    unexplained = ~(is_tie | at_edge)
    assert not unexplained.any(), (
        f"{int(unexplained.sum())} differing lines are neither a "
        f"nearest-neighbour tie nor an array edge: {lines[unexplained].tolist()}"
    )
    for name, mask in edges.items():
        assert int(mask.sum()) <= 1, f"{int(mask.sum())} differing lines on {name}"
