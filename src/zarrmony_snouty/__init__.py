"""zarrmony-snouty — Snouty (AndrewGYork SOLS) reader plugin for zarrmony.

The package ships two ``ReaderPlugin`` values, both registered under the
``zarrmony.readers`` entry point declared in ``pyproject.toml``:

- ``plugin`` — the v0.1 subdir-level matcher; fires on an individual
  ``*_ht_sols_snap``/``*_ht_sols_acquire`` acquisition directory.
- ``session_plugin`` — the v0.3 session-level matcher; fires on a parent
  ``*_ht_sols_gui`` directory and fans out to one output store per
  non-empty child subdir in a single ``zarrmony convert`` invocation.

End users do not import from this package directly; they
``pip install zarrmony-snouty`` and zarrmony picks the plugins up
automatically.
"""

import os
from pathlib import Path

from zarrmony.readers.plugin import ReaderPlugin

from .adapter import DEFAULT_MODE, SnoutyReader
from .match import match, match_session
from .session import (
    SnoutySessionLayoutWarning,
    SnoutySessionReader,
    SnoutySubdirSkippedWarning,
)

__all__ = [
    "SnoutyReader",
    "SnoutySessionLayoutWarning",
    "SnoutySessionReader",
    "SnoutySubdirSkippedWarning",
    "match",
    "match_session",
    "plugin",
    "session_plugin",
]

_MODE_ENV_VAR = "ZARRMONY_SNOUTY_MODE"

# Mode is the output geometry, engine is where it computes. Two separate
# vars, because they are independent choices: ``traditional`` is the only
# mode with a GPU path, and it still runs on the CPU by default.
_ENGINE_ENV_VAR = "ZARRMONY_SNOUTY_ENGINE"


def _open(path: Path) -> SnoutyReader:
    # ReaderPlugin.open only takes a path, so mode and engine are opted in
    # through env vars — SnoutyReader validates both values and raises
    # SnoutyModeError or SnoutyEngineError on an unknown one.
    mode = os.environ.get(_MODE_ENV_VAR, DEFAULT_MODE)
    engine = os.environ.get(_ENGINE_ENV_VAR, "auto")
    return SnoutyReader(path, mode=mode, engine=engine)


def _open_session(path: Path) -> SnoutySessionReader:
    # Same env-var contract as the subdir plugin; the session reader forwards
    # ``mode`` to every child SnoutyReader it instantiates, so a single
    # ``ZARRMONY_SNOUTY_MODE=desheared`` deshears every subdir in the batch.
    # ``engine`` is resolved by the session itself, which under ``auto`` picks
    # one engine for the whole batch.
    mode = os.environ.get(_MODE_ENV_VAR, DEFAULT_MODE)
    engine = os.environ.get(_ENGINE_ENV_VAR, "auto")
    return SnoutySessionReader(path, mode=mode, engine=engine)


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
