"""The shared error base.

Lives in its own module so that low-level modules such as
:mod:`zarrmony_snouty._hostmem` can raise a ``SnoutyError`` without importing
:mod:`zarrmony_snouty.adapter`, which imports them in turn. ``adapter``
re-exports ``SnoutyError``, so the public import path is unchanged.
"""

from __future__ import annotations


class SnoutyError(Exception):
    """Base class for zarrmony-snouty errors."""
