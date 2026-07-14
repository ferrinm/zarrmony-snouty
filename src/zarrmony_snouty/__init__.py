"""zarrmony-snouty — Snouty (AndrewGYork SOLS) reader plugin for zarrmony.

Importing this package exposes a ``plugin`` value (a ``ReaderPlugin``) that is
also surfaced via the ``zarrmony.readers`` entry point declared in
``pyproject.toml``. End users do not import from this package directly; they
``pip install zarrmony-snouty`` and zarrmony picks the plugin up automatically.
"""

from pathlib import Path

from zarrmony.readers.plugin import ReaderPlugin

from .adapter import SnoutyReader
from .match import match

__all__ = ["SnoutyReader", "match", "plugin"]


def _open(path: Path) -> SnoutyReader:
    return SnoutyReader(path)


plugin = ReaderPlugin(
    name="zarrmony-snouty",
    match=match,
    open=_open,
    distribution="zarrmony-snouty",
    source="entry_point",
)
