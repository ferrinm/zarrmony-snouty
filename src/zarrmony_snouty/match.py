"""Cheap predicates that identify Snouty acquisition inputs.

Three matchers ship in this module:

- ``match`` fires on an individual acquisition subdirectory whose name ends in
  ``_ht_sols_snap`` or ``_ht_sols_acquire`` and which contains sibling
  ``data/`` and ``metadata/`` dirs with at least one ``.tif`` and one ``.txt``.
  This is v0.1's unit of input — one match, one conversion, one output store.
- ``match_session`` fires on the parent GUI-session directory whose name ends
  in ``_ht_sols_gui`` and which contains at least one immediate
  ``_ht_sols_snap``/``_ht_sols_acquire`` child. This is v0.3's unit of input —
  one match, one conversion, N output stores (one per non-empty child subdir).
- ``match_plate`` fires on a multiwell-plate acquisition and returns 200. It
  tests the contents only, never the directory name, because an operator
  writes that name by hand and two of the ten plate acquisitions on the share
  carry no plate token at all. See ADR-0003.

Every matcher is side-effect-cheap: no metadata parsing, no recursive walk,
early return on first evidence. The session matcher deliberately does **not**
require ``XY_stage_position_list.txt`` because some GUI sessions never move
the stage and never write the file.

The plate matcher reads filenames out of one ``iterdir()`` and never calls
``stat()``, because a full 384-well plate holds 3456 of them and the share is
a network mount. A name that ends in ``.tif`` is a file; Snouty writes no
directory with that name.
"""

import re
from pathlib import Path
from typing import Literal

_SUBDIR_SUFFIXES = ("_ht_sols_snap", "_ht_sols_acquire")
_SESSION_SUFFIX = "_ht_sols_gui"

#: Grammar A — the vendor ``get_multiwell_plate_positions`` generator. The
#: well is a row letter plus a 1-based column, and ``r<NN>c<NN>`` after it is
#: the **field** inside that well.
PLATE_A_RE = re.compile(
    r"^(\d{6})_(?P<row>[A-P])(?P<col>\d{2})r(?P<frow>\d{2})c(?P<fcol>\d{2})\.tif$"
)

#: Grammar B — a hand-rolled operator loop, ``well = 'r%02ic%02i' % (r, c)``.
#: Here ``r<NN>c<NN>`` is the **well**, both indices are 0-based, and there is
#: exactly one field per well. Both patterns are anchored on purpose: grammar
#: B's token is byte-identical to grammar A's field suffix, so an unanchored
#: search reads a 384-well grammar-B plate as 384 fields of one well.
PLATE_B_RE = re.compile(r"^(\d{6})_r(?P<row>\d{2})c(?P<col>\d{2})\.tif$")

PlateGrammar = Literal["A", "B"]


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
        if entry.is_dir() and any(entry.name.endswith(suffix) for suffix in _SUBDIR_SUFFIXES):
            return 100
    return None


def match_plate(path: Path) -> int | None:
    """Fire on a multiwell-plate acquisition directory. Return 200.

    200 beats the 100 that ``match`` and ``match_session`` return, so a plate
    still converts as a plate if an operator ever gives a plate directory a
    GUI suffix.

    A directory whose ``data/`` mixes the two grammars does not match here.
    The plate reader raises ``SnoutyDataError`` on that shape, and this
    predicate must not raise: zarrmony calls every matcher on every input.
    """
    if not path.is_dir():
        return None
    data_dir = path / "data"
    if not (data_dir.is_dir() and (path / "metadata").is_dir()):
        return None
    if plate_grammar(data_dir) is None:
        return None
    return 200


def plate_grammar(data_dir: Path) -> PlateGrammar | None:
    """Return the one grammar every ``.tif`` in ``data_dir`` follows.

    ``None`` when the directory holds no ``.tif``, when a ``.tif`` matches
    neither grammar, or when two ``.tif`` files follow different grammars.
    Non-``.tif`` entries are ignored. The plate reader re-does this walk
    strictly, with an error per rejected file; this one only answers yes or
    no, as a matcher must.
    """
    grammar: PlateGrammar | None = None
    for entry in data_dir.iterdir():
        if not entry.name.endswith(".tif"):
            continue
        if PLATE_A_RE.match(entry.name):
            found: PlateGrammar = "A"
        elif PLATE_B_RE.match(entry.name):
            found = "B"
        else:
            return None
        if grammar is not None and found != grammar:
            return None
        grammar = found
    return grammar


def _dir_has_suffix(directory: Path, suffix: str) -> bool:
    for entry in directory.iterdir():
        if entry.is_file() and entry.name.endswith(suffix):
            return True
    return False
