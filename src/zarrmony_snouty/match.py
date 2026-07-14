"""Cheap predicate that identifies a Snouty acquisition subdirectory.

The Snouty GUI writes each snap/acquire run into its own subdirectory whose
name ends in ``_ht_sols_snap`` or ``_ht_sols_acquire`` and which contains
``data/`` and ``metadata/`` sibling directories. Matching that subdir directly
(as opposed to the parent GUI session dir) mirrors how ``zarrmony-blaze``
consumes a single experiment directory: one match, one conversion, one output
store.

The suffix check keeps the matcher specific — it refuses to fire on unrelated
directories that happen to have ``data/`` and ``metadata/`` children.
Side-effect-free: at most three ``iterdir()``s, early return on first hit.
"""

from pathlib import Path

_SUFFIXES = ("_ht_sols_snap", "_ht_sols_acquire")


def match(path: Path) -> int | None:
    if not path.is_dir():
        return None
    if not any(path.name.endswith(suffix) for suffix in _SUFFIXES):
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


def _dir_has_suffix(directory: Path, suffix: str) -> bool:
    for entry in directory.iterdir():
        if entry.is_file() and entry.name.endswith(suffix):
            return True
    return False
