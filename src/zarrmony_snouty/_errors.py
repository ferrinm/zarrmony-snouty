"""The shared error base, and the errors that more than one module raises.

Lives in its own module so that low-level modules such as
:mod:`zarrmony_snouty._hostmem` and :mod:`zarrmony_snouty._pixels` can raise
a ``SnoutyError`` without importing :mod:`zarrmony_snouty.adapter`, which
imports them in turn. ``adapter`` re-exports every error here, so the public
import path is unchanged.
"""

from __future__ import annotations

from zarrmony.errors import ReaderKwargError


class SnoutyError(Exception):
    """Base class for zarrmony-snouty errors."""


class SnoutyEngineError(SnoutyError, ValueError):
    """The GPU engine was asked for and cannot run.

    Covers an unusable environment (cupy missing, or no CUDA device) and an
    unrecognised ``engine`` value. A ``ValueError`` subclass, mirroring
    ``SnoutyModeError``.
    """


class SnoutyDataError(SnoutyError):
    """The acquisition directory has no readable data files, or its files
    cannot be mapped onto scenes.

    Raised by the flat reader when ``data/`` mixes multi-position
    (``_pNNNNNN.tif``) and non-position naming, and by the plate reader when
    ``data/`` mixes the two well-label grammars or holds a ``.tif`` that
    matches neither.
    """


class SnoutyModeError(SnoutyError, ValueError):
    """The ``mode`` kwarg (or ``ZARRMONY_SNOUTY_MODE`` env var) is not one of
    ``raw`` / ``desheared`` / ``traditional``."""


class SnoutyReaderKwargError(SnoutyError, ReaderKwargError):
    """A reader kwarg reached a plugin ``open``, and these take none.

    zarrmony forwards ``--reader-kwarg KEY=VALUE`` verbatim to the winning
    plugin's ``open``. Without this error Python's argument binding raises a
    ``TypeError``, which the zarrmony CLI does not catch, so the user gets a
    stack trace (#42).

    ``zarrmony.errors.ReaderKwargError`` is one of the types that CLI turns
    into a one-line message, and it is the type zarrmony defines for this
    situation. ``SnoutyError`` keeps the error in this package's family, so a
    library caller that catches ``SnoutyError`` still catches it.
    """


class SnoutyVolumesPerBufferUnsupportedError(SnoutyError, NotImplementedError):
    """The sidecar reports ``volumes_per_buffer > 1``.

    Snouty's hardware-limited time sampling packs multiple volumes into a
    single ``.tif`` (frames laid out as
    ``(volumes_per_buffer, slices_per_volume, channels, Y, X)``). We have
    not yet staged a real ``volumes_per_buffer > 1`` fixture, so the
    buffer-frame layout inside a single ``.tif`` remains unverified.
    """
