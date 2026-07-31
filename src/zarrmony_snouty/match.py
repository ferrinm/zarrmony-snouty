"""Cheap predicates that identify Snouty acquisition inputs.

Two matchers ship in this module:

- ``match`` fires on an individual acquisition subdirectory whose name ends in
  ``_ht_sols_snap`` or ``_ht_sols_acquire`` and which contains sibling
  ``data/`` and ``metadata/`` dirs with at least one ``.tif`` and one ``.txt``.
  This is v0.1's unit of input — one match, one conversion, one output store.
- ``match_session`` fires on the parent GUI-session directory whose name ends
  in ``_ht_sols_gui`` and which contains at least one immediate
  ``_ht_sols_snap``/``_ht_sols_acquire`` child. This is v0.3's unit of input —
  one match, one conversion, N output stores (one per non-empty child subdir).

Both matchers are side-effect-cheap: no metadata parsing, no recursive walk,
early return on first evidence. The session matcher deliberately does **not**
require ``XY_stage_position_list.txt`` because some GUI sessions never move
the stage and never write the file.
"""

from pathlib import Path

_SUBDIR_SUFFIXES = ("_ht_sols_snap", "_ht_sols_acquire")
_SESSION_SUFFIX = "_ht_sols_gui"


def match(path: Path) -> int | None:
    if not path.is_dir():
        return None
    if not any(path.name.endswith(suffix) for suffix in _SUBDIR_SUFFIXES):
        return None
    data_dir = path / "data"
    metadata_dir = path / "metadata"
    if not (data_dir.is_dir() and metadata_dir.is_dir()):
        return None
    if not _dir_has_suffix(data_dir, ".tif"):
        return None
    if not _dir_has_suffix(metadata_dir, ".txt"):
        return None
    return 100


def match_session(path: Path) -> int | None:
    if not path.is_dir():
        return None
    if not path.name.endswith(_SESSION_SUFFIX):
        return None
    for entry in path.iterdir():
        if entry.is_dir() and any(
            entry.name.endswith(suffix) for suffix in _SUBDIR_SUFFIXES
        ):
            return 100
    return None


def _dir_has_suffix(directory: Path, suffix: str) -> bool:
    for entry in directory.iterdir():
        if entry.is_file() and entry.name.endswith(suffix):
            return True
    return False
