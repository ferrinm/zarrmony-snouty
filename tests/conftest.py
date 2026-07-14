"""Synthetic Snouty acquisition-subdirectory fixture.

Writes the minimal on-disk shape the adapter reads: a directory named
``<ts>_000_ht_sols_snap/`` containing ``data/snap.tif`` (a real 16-bit
``(Z, Y, X)`` volume written with ``tifffile``) and ``metadata/snap.txt``
mirroring the vendor's key=value sidecar. Every plane is filled with a
distinct value so per-plane asserts can check both shape and content.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
import tifffile

from zarrmony_snouty._metadata import TIMESTAMP_STRIP_PX


@dataclass(frozen=True)
class SnoutyFixture:
    dir: Path
    size_z: int
    size_y: int  # AFTER timestamp-strip crop
    size_x: int
    height_px: int  # BEFORE crop
    channels: tuple[str, ...]
    sample_px_um: float
    scan_step_size_um: float
    voxel_aspect_ratio: float
    scan_step_size_px: float

    def value_for(self, z: int) -> int:
        # Distinct per-plane fill so adapter tests can check the crop boundary
        # and Z ordering without relying on all-zeros arrays.
        return z + 1


def _sidecar_text(fixture: SnoutyFixture) -> str:
    lines = [
        "Date: 2026-07-14",
        "Time: 10:15:35",
        "filename: snap.tif",
        f"folder_name: {fixture.dir.parent.name}\\{fixture.dir.name}",
        f"channels_per_slice: {fixture.channels!r}",
        f"height_px: {fixture.height_px}",
        f"width_px: {fixture.size_x}",
        f"slices_per_volume: {fixture.size_z}",
        "volumes_per_buffer: 1",
        f"sample_px_um: {fixture.sample_px_um}",
        f"scan_step_size_um: {fixture.scan_step_size_um}",
        f"voxel_aspect_ratio: {fixture.voxel_aspect_ratio}",
        f"scan_step_size_px: {fixture.scan_step_size_px}",
        "tilt_deg: 55.0",
        "autofocus_enabled: False",
        "display: True",
    ]
    return "\n".join(lines) + "\n"


def write_synthetic_snouty(
    root: Path,
    *,
    subdir_name: str = "2026-07-14_10-15-35_000_ht_sols_snap",
    size_z: int = 4,
    size_y_cropped: int = 6,
    size_x: int = 8,
    channels: tuple[str, ...] = ("LED",),
    sample_px_um: float = 0.1755,
    scan_step_size_um: float = 2.14,
    voxel_aspect_ratio: float = 9.997,
    scan_step_size_px: float = 7.0,
) -> SnoutyFixture:
    """Write a synthetic Snouty subdirectory under ``root``.

    Returns a ``SnoutyFixture`` with everything a test needs to assert against.
    """
    subdir = root / subdir_name
    (subdir / "data").mkdir(parents=True)
    (subdir / "metadata").mkdir()
    (subdir / "preview").mkdir()

    height_px = size_y_cropped + TIMESTAMP_STRIP_PX
    fixture = SnoutyFixture(
        dir=subdir,
        size_z=size_z,
        size_y=size_y_cropped,
        size_x=size_x,
        height_px=height_px,
        channels=channels,
        sample_px_um=sample_px_um,
        scan_step_size_um=scan_step_size_um,
        voxel_aspect_ratio=voxel_aspect_ratio,
        scan_step_size_px=scan_step_size_px,
    )

    volume = np.zeros((size_z, height_px, size_x), dtype=np.uint16)
    for z in range(size_z):
        # Timestamp strip at the top rows — filled with a sentinel so tests can
        # confirm it gets cropped and never surfaces to callers.
        volume[z, :TIMESTAMP_STRIP_PX, :] = 9999
        volume[z, TIMESTAMP_STRIP_PX:, :] = fixture.value_for(z)
    # photometric="minisblack" silences a future-default deprecation warning in
    # tifffile for small (small_dim, ..., 8) test arrays that its heuristic
    # currently interprets as RGB planes.
    tifffile.imwrite(subdir / "data" / "snap.tif", volume, photometric="minisblack")

    (subdir / "metadata" / "snap.txt").write_text(_sidecar_text(fixture))

    return fixture


@pytest.fixture
def synthetic_snouty(tmp_path: Path) -> SnoutyFixture:
    return write_synthetic_snouty(tmp_path)
