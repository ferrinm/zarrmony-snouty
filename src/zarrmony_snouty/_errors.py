"""The shared error base.

Lives in its own module so that low-level modules such as
:mod:`zarrmony_snouty._hostmem` can raise a ``SnoutyError`` without importing
:mod:`zarrmony_snouty.adapter`, which imports them in turn. ``adapter``
re-exports ``SnoutyError``, so the public import path is unchanged.
"""

from __future__ import annotations


class SnoutyError(Exception):
    """Base class for zarrmony-snouty errors."""


class SnoutyEngineError(SnoutyError, ValueError):
    """The GPU engine was asked for and cannot run.

    Covers an unusable environment (cupy missing, or no CUDA device) and an
    unrecognised ``engine`` value. A ``ValueError`` subclass, mirroring
    ``SnoutyModeError``.
    """
