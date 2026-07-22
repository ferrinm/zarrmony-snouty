"""zarrmony-snouty — Snouty (AndrewGYork SOLS) reader plugin for zarrmony.

Importing this package exposes a ``plugin`` value (a ``ReaderPlugin``) that is
also surfaced via the ``zarrmony.readers`` entry point declared in
``pyproject.toml``. End users do not import from this package directly; they
``pip install zarrmony-snouty`` and zarrmony picks the plugin up automatically.
"""

import os
from pathlib import Path

from zarrmony.readers.plugin import ReaderPlugin

from .adapter import SnoutyReader
from .match import match

__all__ = ["SnoutyReader", "match", "plugin"]

_MODE_ENV_VAR = "ZARRMONY_SNOUTY_MODE"


def _open(path: Path) -> SnoutyReader:
    # ReaderPlugin.open only takes a path, so mode is opted in through an env
    # var — SnoutyReader validates the value and raises SnoutyModeError on an
    # unknown mode.
    mode = os.environ.get(_MODE_ENV_VAR, "raw")
    return SnoutyReader(path, mode=mode)


plugin = ReaderPlugin(
    name="zarrmony-snouty",
    match=match,
    open=_open,
    distribution="zarrmony-snouty",
    source="entry_point",
)
