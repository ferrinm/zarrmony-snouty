"""zarrmony-snouty — Snouty (AndrewGYork SOLS) reader plugin for zarrmony.

The package ships three ``ReaderPlugin`` values, all registered under the
``zarrmony.readers`` entry point declared in ``pyproject.toml``:

- ``plugin`` — the v0.1 acquisition-level matcher; fires on a directory with
  ``data/`` and ``metadata/`` and a Snouty sidecar.
- ``session_plugin`` — the v0.3 session-level matcher; fires on a parent
  directory holding such a child, and fans out to one output store per valid
  child in a single ``zarrmony convert`` invocation.
- ``plate_plugin`` — the multiwell-plate matcher; fires on an acquisition
  whose ``data/`` filenames carry well coordinates, and writes one OME-NGFF
  HCS plate store. It outranks the other two.

No matcher tests the directory name. An operator names most runs, so the
name is not evidence. See ADR-0003 and its 2026-10-06 amendment.

None of the three accepts a reader kwarg. ``ZARRMONY_SNOUTY_MODE`` and
``ZARRMONY_SNOUTY_ENGINE`` are the controls, and they are environment
variables. A ``--reader-kwarg`` on a Snouty input raises
``SnoutyReaderKwargError`` before the reader opens a file (#42).

End users do not import from this package directly; they
``pip install zarrmony-snouty`` and zarrmony picks the plugins up
automatically.
"""

import os
from pathlib import Path

from zarrmony.readers.plugin import ReaderPlugin

from ._errors import SnoutyReaderKwargError
from .adapter import DEFAULT_MODE, SnoutyReader
from .match import match, match_plate, match_session
from .plate import (
    SnoutyPlateFormatError,
    SnoutyPlateFormatWarning,
    SnoutyPlateReader,
)
from .session import (
    SnoutySessionLayoutWarning,
    SnoutySessionReader,
    SnoutySubdirSkippedWarning,
)

__all__ = [
    "SnoutyPlateFormatError",
    "SnoutyPlateFormatWarning",
    "SnoutyPlateReader",
    "SnoutyReader",
    "SnoutyReaderKwargError",
    "SnoutySessionLayoutWarning",
    "SnoutySessionReader",
    "SnoutySubdirSkippedWarning",
    "match",
    "match_plate",
    "match_session",
    "plate_plugin",
    "plugin",
    "session_plugin",
]

_MODE_ENV_VAR = "ZARRMONY_SNOUTY_MODE"

# Mode is the output geometry, engine is where it computes. Two separate
# vars, because they are independent choices: ``traditional`` is the only
# mode with a GPU path, and it still runs on the CPU by default.
_ENGINE_ENV_VAR = "ZARRMONY_SNOUTY_ENGINE"


def _reject_reader_kwargs(reader_kwargs: dict[str, object]) -> None:
    """Refuse every reader kwarg, and name each one that was passed.

    See :class:`SnoutyReaderKwargError` for why these readers reject rather
    than ignore. Called before the reader is constructed, so no directory is
    scanned and no file under ``data/`` is opened.
    """
    if not reader_kwargs:
        return
    names = ", ".join(repr(name) for name in sorted(reader_kwargs))
    noun = "kwarg" if len(reader_kwargs) == 1 else "kwargs"
    raise SnoutyReaderKwargError(
        f"unsupported reader {noun} {names}. The Snouty readers accept no "
        f"reader kwargs. Set {_MODE_ENV_VAR} for the output mode, or "
        f"{_ENGINE_ENV_VAR} for the compute engine."
    )


def _open(path: Path, /, **reader_kwargs: object) -> SnoutyReader:
    # The only supported input is a path, so mode and engine are opted in
    # through env vars — SnoutyReader validates both values and raises
    # SnoutyModeError or SnoutyEngineError on an unknown one. The ``**kwargs``
    # exist to reject a reader kwarg with a sentence, never to accept one.
    #
    # ``path`` is positional-only on all three shims, and must stay that way.
    # zarrmony passes the input positionally, so a named ``path`` would bind
    # twice and raise the very TypeError this rejects.
    _reject_reader_kwargs(reader_kwargs)
    mode = os.environ.get(_MODE_ENV_VAR, DEFAULT_MODE)
    engine = os.environ.get(_ENGINE_ENV_VAR, "auto")
    return SnoutyReader(path, mode=mode, engine=engine)


def _open_session(path: Path, /, **reader_kwargs: object) -> SnoutySessionReader:
    # Same env-var contract as the subdir plugin; the session reader forwards
    # ``mode`` to every child SnoutyReader it instantiates, so a single
    # ``ZARRMONY_SNOUTY_MODE=desheared`` deshears every subdir in the batch.
    # ``engine`` is resolved by the session itself, which under ``auto`` picks
    # one engine for the whole batch.
    _reject_reader_kwargs(reader_kwargs)
    mode = os.environ.get(_MODE_ENV_VAR, DEFAULT_MODE)
    engine = os.environ.get(_ENGINE_ENV_VAR, "auto")
    return SnoutySessionReader(path, mode=mode, engine=engine)


def _open_plate(path: Path, /, **reader_kwargs: object) -> SnoutyPlateReader:
    # Same env-var contract as the other two plugins. ``plate_format`` gets no
    # env var: it is a per-plate correction, not a batch setting, and one
    # stale value would mislabel every plate in a run.
    _reject_reader_kwargs(reader_kwargs)
    mode = os.environ.get(_MODE_ENV_VAR, DEFAULT_MODE)
    engine = os.environ.get(_ENGINE_ENV_VAR, "auto")
    return SnoutyPlateReader(path, mode=mode, engine=engine)


plugin = ReaderPlugin(
    name="zarrmony-snouty",
    match=match,
    open=_open,
    distribution="zarrmony-snouty",
    source="entry_point",
)

session_plugin = ReaderPlugin(
    name="zarrmony-snouty-session",
    match=match_session,
    open=_open_session,
    distribution="zarrmony-snouty",
    source="entry_point",
)

plate_plugin = ReaderPlugin(
    name="zarrmony-snouty-plate",
    match=match_plate,
    open=_open_plate,
    distribution="zarrmony-snouty",
    source="entry_point",
)
