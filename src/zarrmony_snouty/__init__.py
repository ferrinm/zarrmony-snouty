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

from .adapter import SnoutyReader
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


def _open(path: Path) -> SnoutyReader:
    # ReaderPlugin.open only takes a path, so mode is opted in through an env
    # var — SnoutyReader validates the value and raises SnoutyModeError on an
    # unknown mode.
    mode = os.environ.get(_MODE_ENV_VAR, "raw")
    return SnoutyReader(path, mode=mode)


def _open_session(path: Path) -> SnoutySessionReader:
    # Same env-var contract as the subdir plugin; the session reader forwards
    # ``mode`` to every child SnoutyReader it instantiates, so a single
    # ``ZARRMONY_SNOUTY_MODE=desheared`` deshears every subdir in the batch.
    mode = os.environ.get(_MODE_ENV_VAR, "raw")
    return SnoutySessionReader(path, mode=mode)


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
