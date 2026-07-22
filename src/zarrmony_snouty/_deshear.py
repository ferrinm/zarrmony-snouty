"""CPU deshear and traditional-view transforms for Snouty volumes.

Ported from Austin Lefebvre's ``snouty-folder`` package
(https://github.com/aelefebv/snouty-folder — specifically
``snouty_folder.SnoutyFolder._per_slice_cpu_deshear`` and ``_affine_rotate``)
— the CPU paths only. cupy/GPU is intentionally out of scope for v0.2 to keep
the install footprint small.

The transforms operate on a bare ``(Z, Y, X)`` volume; the adapter is
responsible for adding leading ``T`` and ``C`` axes back on. Output-shape
formulas live alongside the transforms so the reader can advertise a dask
shape without materializing the array.

Geometry recap:
- ``deshear`` per-slice shifts each z-plane along Y by ``round(scan_step_size_px * z)``
  and pads Y by the maximum shift. Physical spacing does not change — only
  axes align.
- ``traditional`` deshears, then applies a scipy ``affine_transform`` rotating
  by ``arctan(scan_step_size_px / voxel_aspect_ratio)`` about the X axis with
  Z zoom applied first, then swaps Y/Z and flips Z. The result is an
  orthogonal top-down view with Z spacing ``sample_px_um * voxel_aspect_ratio``.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import affine_transform


def max_deshear_shift(scan_step_size_px: float, size_z: int) -> int:
    """Maximum per-plane Y shift, in pixels, for a size_z-slice volume."""
    return int(np.rint(scan_step_size_px * (size_z - 1)))


def desheared_shape(
    size_z: int, size_y: int, size_x: int, scan_step_size_px: float
) -> tuple[int, int, int]:
    """Output ``(Z, Y, X)`` shape for :func:`deshear_zyx`."""
    return (size_z, size_y + max_deshear_shift(scan_step_size_px, size_z), size_x)


def traditional_shape(
    size_z: int,
    size_y: int,
    size_x: int,
    scan_step_size_px: float,
    voxel_aspect_ratio: float,
) -> tuple[int, int, int]:
    """Output ``(Z, Y, X)`` shape for :func:`traditional_zyx`.

    Mirrors ``snouty_folder.SnoutyFolder._load_traditional_dims``: after the
    affine rotate + swap, the axis stored in the Z slot has length
    ``round(sin(atan(scan_step_size_px)) * size_y)`` and the axis in the Y
    slot has length ``round((size_z * aspect / cos(atan(scan_step_size_px /
    aspect))) + (cos(atan(scan_step_size_px)) * size_y / aspect))``.
    """
    final_rotation_angle = np.arctan(scan_step_size_px / voxel_aspect_ratio)
    initial_rotation_angle = np.arctan(scan_step_size_px)
    y_rotated = int(np.rint(np.sin(initial_rotation_angle) * size_y))
    z_rotated = int(
        np.rint(
            (size_z * voxel_aspect_ratio / np.cos(final_rotation_angle))
            + (np.cos(initial_rotation_angle) * size_y / voxel_aspect_ratio)
        )
    )
    return (y_rotated, z_rotated, size_x)


def deshear_zyx(volume: np.ndarray, scan_step_size_px: float) -> np.ndarray:
    """Per-slice CPU deshear of a ``(Z, Y, X)`` volume.

    Each z-plane is shifted along Y by ``round(scan_step_size_px * z)`` into a
    freshly-zeroed output of shape :func:`desheared_shape`. Same algorithm as
    ``snouty_folder._per_slice_cpu_deshear`` with the T/C loops elided (the
    adapter only ever hands us a single volume in v0.2).
    """
    size_z, size_y, size_x = volume.shape
    out = np.zeros(desheared_shape(size_z, size_y, size_x, scan_step_size_px), dtype=volume.dtype)
    for z in range(size_z):
        shift = int(np.rint(scan_step_size_px * z))
        out[z, shift : shift + size_y, :] = volume[z, :, :]
    return out


def traditional_zyx(
    volume: np.ndarray, scan_step_size_px: float, voxel_aspect_ratio: float
) -> np.ndarray:
    """Deshear a ``(Z, Y, X)`` volume then rotate into a top-down orthogonal view.

    The rotation angle and matrix come straight from
    ``snouty_folder._affine_rotate`` + ``_affine_matrix``. ``order=0`` +
    ``prefilter=False`` matches the reference (nearest-neighbour, no spline
    prefilter) so intensities stay integer-valued and no interpolation smears
    the boundary between padded zeros and real signal.
    """
    size_z, size_y, size_x = volume.shape
    desheared = deshear_zyx(volume, scan_step_size_px)
    y_rotated, z_rotated, _ = traditional_shape(
        size_z, size_y, size_x, scan_step_size_px, voxel_aspect_ratio
    )
    rotation_angle = scan_step_size_px / voxel_aspect_ratio
    matrix = np.linalg.inv(_affine_matrix(rotation_angle, voxel_aspect_ratio))
    rotated = affine_transform(
        desheared,
        matrix=matrix,
        offset=np.zeros(3, dtype=np.float64),
        order=0,
        prefilter=False,
        output_shape=(z_rotated, y_rotated, size_x),
    )
    rotated = np.swapaxes(rotated, 0, 1)
    return np.flip(rotated, axis=0)


def _affine_matrix(rotation_angle: float, z_zoom: float) -> np.ndarray:
    theta = np.arctan(rotation_angle)
    c, s = np.cos(theta), np.sin(theta)
    scale = np.array([[z_zoom, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float64)
    rotation = np.array([[c, s, 0], [-s, c, 0], [0, 0, 1]], dtype=np.float64)
    return rotation @ scale
