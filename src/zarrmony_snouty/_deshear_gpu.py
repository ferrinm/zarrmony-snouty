"""GPU ``traditional`` transform for Snouty volumes.

Ported from Austin Lefebvre's ``snouty-folder`` package
(https://github.com/aelefebv/snouty-folder — specifically
``snouty_folder.SnoutyFolder._affine_rotate``), the cupy path. The CPU
equivalents live in :mod:`zarrmony_snouty._deshear` and come from the same
source.

Scope is ``traditional`` only (ADR-0002, decision 2). There is no GPU
``deshear``: the CPU deshear costs 0.15 s per timepoint at real geometry, and
a PCIe upload of the input volume alone costs more than that. So the deshear
runs on the CPU here too, and only the desheared array crosses the bus.

cupy is a soft dependency. The module imports cleanly without it and reports
:data:`cupy_available` as ``False``.
"""

from __future__ import annotations

import threading

import numpy as np

from . import _deshear
from ._errors import SnoutyEngineError

try:  # pragma: no cover - the branch taken depends on the install
    import cupy
    import cupyx.scipy.ndimage as _cundi
except ImportError:  # pragma: no cover
    cupy = None
    _cundi = None

#: Whether the cupy soft import succeeded. Callers inspect this to decide
#: whether a GPU path exists at all, without catching ImportError themselves.
cupy_available: bool = cupy is not None


#: Serializes every device allocation this module makes (ADR-0002,
#: decision 3). One ``traditional`` call holds 4.17 GB on the device at the
#: measured geometry. Dask runs ``os.cpu_count()`` threads by default, so 16
#: threads would demand 67 GB from a 23.46 GiB card, and cupy's default memory
#: pool retains freed blocks, which makes the peak worse. The lock fixes
#: device demand at one call's worth whatever the thread count.
_DEVICE_LOCK = threading.Lock()


def traditional_zyx(
    volume: np.ndarray, scan_step_size_px: float, voxel_aspect_ratio: float
) -> np.ndarray:
    """Deshear a ``(Z, Y, X)`` volume then rotate it on the GPU.

    Takes and returns numpy arrays. The upload and the download happen inside
    this module, so a caller never needs to know that cupy exists.

    Mirrors :func:`_deshear.traditional_zyx` step for step, with
    ``cupyx.scipy.ndimage.affine_transform`` in place of the scipy one. The
    two do **not** produce identical pixels. See the note below.

    The deshear runs on the CPU and stays outside the lock, because it
    allocates host memory rather than device memory. Only the upload, the
    rotate, and the download are serialized.

    Raises:
        SnoutyEngineError: cupy is not installed.

    Note:
        This result is not byte-identical to the CPU one. At the measured
        geometry 0.0036% of voxels differ, on 29 lines of the 808,780 in the
        rotated grid. 28 of those are nearest-neighbour ties, where the two
        libraries pick opposite equidistant voxels and both answers are
        equally valid. **The remaining line is a real loss:** its source
        coordinate is exactly ``0.0``, scipy reads the first plane there and
        cupy returns the fill value, so the GPU drops 1500 voxels at the
        outermost non-empty edge plane. Measured in issue #18.
    """
    desheared = _deshear.deshear_zyx(volume, scan_step_size_px)
    size_z, size_y, size_x = volume.shape
    y_rotated, z_rotated, _ = _deshear.traditional_shape(
        size_z, size_y, size_x, scan_step_size_px, voxel_aspect_ratio
    )
    with _DEVICE_LOCK:
        return _rotate_on_device(
            desheared,
            scan_step_size_px=scan_step_size_px,
            voxel_aspect_ratio=voxel_aspect_ratio,
            output_shape=(z_rotated, y_rotated, size_x),
        )


def _rotate_on_device(
    desheared: np.ndarray,
    *,
    scan_step_size_px: float,
    voxel_aspect_ratio: float,
    output_shape: tuple[int, int, int],
) -> np.ndarray:
    """Upload, rotate, swap, flip, download. Call with the lock held."""
    if cupy is None:
        raise SnoutyEngineError(
            "the GPU traditional transform needs cupy, which is not installed; "
            "install a cupy wheel matching the CUDA runtime, or use "
            "zarrmony_snouty._deshear.traditional_zyx for the CPU path"
        )
    rotation_angle = scan_step_size_px / voxel_aspect_ratio
    matrix = np.linalg.inv(_deshear._affine_matrix(rotation_angle, voxel_aspect_ratio))
    rotated = _cundi.affine_transform(
        cupy.asarray(desheared),
        matrix=cupy.asarray(matrix),
        offset=cupy.zeros(3, dtype=cupy.float64),
        order=0,
        prefilter=False,
        output_shape=output_shape,
    )
    return cupy.asnumpy(cupy.flip(cupy.swapaxes(rotated, 0, 1), axis=0))


def required_device_bytes(
    *,
    size_z: int,
    size_y: int,
    size_x: int,
    scan_step_size_px: float,
    voxel_aspect_ratio: float,
    itemsize: int,
) -> int:
    """Peak device bytes one ``traditional`` call holds, for one channel.

    The desheared intermediate plus the rotated output. Derived from the real
    shapes in :mod:`zarrmony_snouty._deshear`, never hardcoded, so a change to
    either shape formula moves the figure with it.

    Smaller than the host footprint :func:`_hostmem.transform_footprint_bytes`
    computes, which also counts the raw input volume. The raw volume is never
    uploaded, because the deshear happens on the CPU.
    """
    desheared = _volume(_deshear.desheared_shape(size_z, size_y, size_x, scan_step_size_px))
    rotated = _volume(
        _deshear.traditional_shape(size_z, size_y, size_x, scan_step_size_px, voxel_aspect_ratio)
    )
    return (desheared + rotated) * itemsize


def free_device_bytes() -> int | None:
    """Free bytes on the current CUDA device, or ``None`` when unknowable.

    ``None`` covers both "cupy is not installed" and "cupy is installed but
    no device answers". A caller needs that apart from a real zero, which
    would mean a device that exists and is full.

    Reports free bytes rather than total. The measured device advertises
    23.46 GiB total, and a driver or another process holds some of it.
    """
    if cupy is None:
        return None
    try:
        free, _total = cupy.cuda.runtime.memGetInfo()
    except Exception:
        # No device, a driver mismatch, or a device in a bad state. Every one
        # of those means the same thing to a caller: do not plan on the GPU.
        return None
    return int(free)


def _volume(shape: tuple[int, int, int]) -> int:
    z, y, x = shape
    return z * y * x
