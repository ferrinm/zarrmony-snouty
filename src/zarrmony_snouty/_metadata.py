"""Parser for the Snouty ``metadata/<name>.txt`` key=value sidecar.

Ported from ``snouty_folder.SnoutyFolder._load_metadata`` (see Austin
Lefebvre's ``snouty-folder`` reference package at
https://github.com/aelefebv/snouty-folder). The vendor writes a plain-text
file, one ``key: value`` per line, with mixed value types (bool, int, float,
tuple). This parser converts each value with ``ast.literal_eval`` where safe
and exposes the fields the adapter needs on a typed dataclass.

The 8-pixel PCO BCD timestamp row along the top of each Y slice is a hardware
artefact of the vendor's PCO camera — every raw plane starts with an 8-px
binary-coded-decimal timestamp strip. ``snouty-folder`` crops it off before
writing OME-TIFF; we do the same before exposing the array to zarrmony.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SnoutyMetadataError(ValueError):
    """The metadata sidecar is missing, unreadable, or missing required fields."""


TIMESTAMP_STRIP_PX = 8


@dataclass(frozen=True)
class SnoutyMetadata:
    """Structured view of a Snouty ``.txt`` sidecar file.

    Only the fields v0.1 consumes are surfaced as attributes. The verbatim
    key→value dict is preserved on ``raw`` and the raw file text on ``raw_text``
    so the audit record can round-trip everything the vendor emitted.
    """

    raw: dict[str, Any]
    raw_text: str
    channels: tuple[str, ...]
    size_t: int
    size_z: int
    size_y: int
    size_x: int
    sample_px_um: float
    scan_step_size_um: float
    voxel_aspect_ratio: float
    scan_step_size_px: float
    timestamp_strip_px: int


def parse_metadata_dir(metadata_dir: Path) -> SnoutyMetadata:
    """Load the first ``.txt`` file in ``metadata_dir`` (by mtime) as a ``SnoutyMetadata``.

    Snouty ``_acquire`` runs write one ``.txt`` per data buffer; the first
    (oldest) file describes the run's fixed geometry and channels. This matches
    the convention in ``snouty-folder``.
    """
    if not metadata_dir.is_dir():
        raise SnoutyMetadataError(f"metadata directory does not exist: {metadata_dir}")
    candidates = sorted(
        (p for p in metadata_dir.iterdir() if p.is_file() and p.suffix == ".txt"),
        key=lambda p: p.stat().st_mtime,
    )
    if not candidates:
        raise SnoutyMetadataError(f"no .txt files in {metadata_dir}")
    return parse_metadata_file(candidates[0])


def parse_metadata_file(path: Path) -> SnoutyMetadata:
    raw_text = path.read_text()
    raw = _parse_key_value_text(raw_text)

    try:
        channels = tuple(str(c) for c in raw["channels_per_slice"])
        size_z = int(raw["slices_per_volume"])
        height_px = int(raw["height_px"])
        size_x = int(raw["width_px"])
        volumes_per_buffer = int(raw["volumes_per_buffer"])
        sample_px_um = float(raw["sample_px_um"])
        scan_step_size_um = float(raw["scan_step_size_um"])
        voxel_aspect_ratio = float(raw["voxel_aspect_ratio"])
        scan_step_size_px = float(raw["scan_step_size_px"])
    except KeyError as exc:
        raise SnoutyMetadataError(f"metadata sidecar {path} missing required key: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise SnoutyMetadataError(
            f"metadata sidecar {path} has invalid value for a required key: {exc}"
        ) from exc

    size_y = height_px - TIMESTAMP_STRIP_PX
    if size_y <= 0:
        raise SnoutyMetadataError(
            f"height_px={height_px} in {path} is smaller than the "
            f"{TIMESTAMP_STRIP_PX}-px PCO timestamp strip"
        )

    return SnoutyMetadata(
        raw=raw,
        raw_text=raw_text,
        channels=channels,
        size_t=volumes_per_buffer,
        size_z=size_z,
        size_y=size_y,
        size_x=size_x,
        sample_px_um=sample_px_um,
        scan_step_size_um=scan_step_size_um,
        voxel_aspect_ratio=voxel_aspect_ratio,
        scan_step_size_px=scan_step_size_px,
        timestamp_strip_px=TIMESTAMP_STRIP_PX,
    )


def _parse_key_value_text(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        out[key] = _coerce_value(value)
    return out


def _coerce_value(value: str) -> Any:
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    # ast.literal_eval handles ints, floats, tuples, lists, strings, and negatives
    # without the eval() code-execution risk that snouty-folder's original uses.
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value
