"""Cheap predicates that identify Snouty acquisition inputs.

Every matcher here tests contents, never the directory name. The vendor GUI
writes a name ending in ``_ht_sols_snap``, ``_ht_sols_acquire`` or
``_ht_sols_gui``, but an operator writes the name for every scripted run, and
a scripted run is the majority case. A survey of the read-only share found
that a name-based matcher rejects 195 of 321 acquisitions (#34). See ADR-0003.

Three matchers ship in this module:

- ``match`` fires on an individual acquisition directory: sibling ``data/``
  and ``metadata/`` dirs, at least one ``.tif`` under ``data/``, and a
  ``metadata/*.txt`` sidecar carrying a quorum of ``REQUIRED_SIDECAR_KEYS``.
  This is v0.1's unit of input — one match, one conversion, one output store.
- ``match_session`` fires on a parent directory holding at least one immediate
  child that ``match`` accepts. This is v0.3's unit of input — one match, one
  conversion, N output stores (one per valid child).
- ``match_plate`` fires on a multiwell-plate acquisition and returns 200,
  which outranks the 100 the other two return.

The sidecar key set is what identifies a Snouty acquisition. A matcher
answers *is this Snouty*, not *is this Snouty and undamaged*. So the test is a
threshold: ``SIDECAR_KEY_QUORUM`` of the keys in ``REQUIRED_SIDECAR_KEYS``,
not all of them.

The threshold is the point. An acquisition with a damaged sidecar is still a
Snouty acquisition, and it must match, so that the reader opens it and raises
``SnoutyMetadataError`` naming the key that is absent. A matcher that demanded
every key would reject it, zarrmony would fall through to bioio, and the user
would get ``UnsupportedFileFormatError`` — which is the useless error this
module exists to stop (#34). Validity belongs to
``_metadata.parse_metadata_file``, which owns the full list.

Cost: ``match`` reads one sidecar, which is about 1 KB. This is a deliberate
relaxation of the older "no metadata parsing in a matcher" rule — ``read_text``
on one small file costs less than the ``iterdir()`` of up to 3456 entries that
``match_plate`` already does on a 384-well plate. No matcher walks
recursively, and each returns on first evidence.

The session matcher deliberately does **not** require
``XY_stage_position_list.txt`` because some GUI sessions never move the stage
and never write the file.

The plate matcher reads filenames out of one ``iterdir()`` and never calls
``stat()``, because a full 384-well plate holds 3456 of them and the share is
a network mount. A name that ends in ``.tif`` is a file; Snouty writes no
directory with that name.
"""

import re
from pathlib import Path
from typing import Literal

#: Keys ``_metadata.parse_metadata_file`` requires. Every one of these names
#: is Snouty vocabulary, which is what makes the set usable as a signature.
#: Keep it in step with that function.
REQUIRED_SIDECAR_KEYS = frozenset(
    {
        "channels_per_slice",
        "slices_per_volume",
        "height_px",
        "width_px",
        "volumes_per_buffer",
        "sample_px_um",
        "scan_step_size_um",
        "scan_step_size_px",
        "voxel_aspect_ratio",
    }
)

#: How many of ``REQUIRED_SIDECAR_KEYS`` a sidecar must carry to count as
#: Snouty. Set below the full count on purpose, so a damaged sidecar still
#: reaches the reader and gets a precise error. Set high enough that another
#: vendor cannot reach it by accident: six of these nine exact names in one
#: key=value file is not a collision anyone will meet.
SIDECAR_KEY_QUORUM = 6

#: One ``key: value`` per line, so a key is a line start up to the first colon.
#: Matching on the line start is what keeps ``scan_step_size_px`` from being
#: found inside a comment or a value that quotes it.
_SIDECAR_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:", re.MULTILINE)

#: A sidecar bigger than this is not a Snouty sidecar. Real ones are ~1 KB.
#: The cap bounds what a matcher reads off a network mount when it meets a
#: directory holding a large ``.txt``.
_SIDECAR_READ_LIMIT_BYTES = 256 * 1024

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
    """Fire on one Snouty acquisition directory. Return 100.

    The directory name is not tested. An operator writes it, so it carries no
    evidence the sidecar does not carry better. See ADR-0003.
    """
    if not path.is_dir():
        return None
    data_dir = path / "data"
    metadata_dir = path / "metadata"
    if not (data_dir.is_dir() and metadata_dir.is_dir()):
        return None
    if not _dir_has_suffix(data_dir, ".tif"):
        return None
    if not has_snouty_sidecar(metadata_dir):
        return None
    return 100


def match_session(path: Path) -> int | None:
    """Fire on a directory holding Snouty acquisition children. Return 100.

    The parent name is not tested either. The GUI writes ``_ht_sols_gui``, but
    the share also holds ``_ht_sols_gui_session`` and operator-made folders
    that group unrelated runs. Those all convert the same way: N children in,
    N stores out.
    """
    if not path.is_dir():
        return None
    try:
        entries = path.iterdir()
    except OSError:
        return None
    for entry in entries:
        if match(entry) is not None:
            return 100
    return None


def has_snouty_sidecar(metadata_dir: Path) -> bool:
    """Return whether ``metadata_dir`` holds a Snouty ``.txt`` sidecar.

    Answers on identity, not validity: ``SIDECAR_KEY_QUORUM`` of
    ``REQUIRED_SIDECAR_KEYS`` is enough. A sidecar that clears the quorum but
    lacks a key the reader needs still returns ``True`` here, so the reader
    opens it and raises ``SnoutyMetadataError`` naming that key.

    Reads the first ``.txt`` by name. The vendor zero-pads these names and
    writes the same key set into every one, so the first is representative
    and sorting by name avoids the ``stat()`` call that sorting by mtime
    needs. ``_metadata.parse_metadata_dir`` does use mtime, because it reads
    the *values*, and only the oldest file's values describe the run.

    Never raises. zarrmony calls every matcher on every input, including
    directories this process cannot read.
    """
    try:
        candidates = sorted(
            p.name for p in metadata_dir.iterdir() if p.is_file() and p.suffix == ".txt"
        )
    except OSError:
        return False
    if not candidates:
        return False
    sidecar = metadata_dir / candidates[0]
    try:
        if sidecar.stat().st_size > _SIDECAR_READ_LIMIT_BYTES:
            return False
        text = sidecar.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    found = set(_SIDECAR_KEY_RE.findall(text))
    return len(REQUIRED_SIDECAR_KEYS & found) >= SIDECAR_KEY_QUORUM


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
    """Whether ``directory`` holds a file with ``suffix``. False if unreadable.

    The share carries acquisition directories this process cannot list.
    ``match_session`` runs ``match`` over every child, so one unreadable child
    must not take the whole session down.
    """
    try:
        entries = directory.iterdir()
    except OSError:
        return False
    try:
        for entry in entries:
            if entry.is_file() and entry.name.endswith(suffix):
                return True
    except OSError:
        return False
    return False
