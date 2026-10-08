"""Reader-kwarg rejection on all three plugin entry points (#42).

zarrmony forwards ``--reader-kwarg KEY=VALUE`` verbatim to the winning
plugin's ``open`` as ``**reader_kwargs``. These three readers take none, so a
reader kwarg used to reach a bare ``(path)`` signature and raise a
``TypeError``. The zarrmony CLI does not catch ``TypeError``, so the user got
a stack trace and no store.

The trigger is routine, not hypothetical. zarrmony prints a
``TileAlignmentWarning`` on every Snouty convert that advises exactly such a
kwarg, and this reader cannot honour it (#45).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from zarrmony.cli import app
from zarrmony.errors import ReaderKwargError

import zarrmony_snouty
from tests.conftest import PlateSpec, write_synthetic_snouty
from zarrmony_snouty import _open, _open_plate, _open_session
from zarrmony_snouty.adapter import SnoutyError, SnoutyReaderKwargError

#: The kwarg the ``TileAlignmentWarning`` tells every Snouty user to pass.
TILE_SIZE_KWARG = {"tile_size": "64,128"}

#: Two kwargs at once. The error must name both, not only the first.
TWO_KWARGS = {"tile_size": "64,128", "dask_tiles": "true"}


def _acquisition(root: Path) -> Path:
    return write_synthetic_snouty(root).dir


def _session(root: Path) -> Path:
    session = root / "2026-07-14_10-12-21_ht_sols_gui"
    session.mkdir(parents=True)
    write_synthetic_snouty(session, subdir_name="2026-07-14_10-15-35_000_ht_sols_snap")
    return session


def _plate(root: Path) -> Path:
    return write_synthetic_snouty(root, plate=PlateSpec()).dir


@dataclass(frozen=True)
class EntryPoint:
    """One plugin ``open`` callable, with a fixture it accepts.

    ``reader_attr`` names the reader class that ``open`` constructs, as it is
    bound in :mod:`zarrmony_snouty`. A test replaces that attribute to prove
    that rejection happens before construction.
    """

    open_reader: Callable[..., Any]
    make_input: Callable[[Path], Path]
    reader_attr: str


ENTRY_POINTS = [
    pytest.param(EntryPoint(_open, _acquisition, "SnoutyReader"), id="acquisition"),
    pytest.param(EntryPoint(_open_session, _session, "SnoutySessionReader"), id="session"),
    pytest.param(EntryPoint(_open_plate, _plate, "SnoutyPlateReader"), id="plate"),
]


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_opens_with_no_reader_kwargs(entry: EntryPoint, tmp_path: Path) -> None:
    """The control. A convert that passes no reader kwarg is untouched."""
    reader = entry.open_reader(entry.make_input(tmp_path))
    assert reader.xarray_dask_data.ndim == 5


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_rejects_the_tile_size_kwarg_the_warning_advises(entry: EntryPoint, tmp_path: Path) -> None:
    with pytest.raises(SnoutyReaderKwargError) as caught:
        entry.open_reader(entry.make_input(tmp_path), **TILE_SIZE_KWARG)
    assert "tile_size" in str(caught.value)


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_rejection_names_every_unknown_kwarg(entry: EntryPoint, tmp_path: Path) -> None:
    with pytest.raises(SnoutyReaderKwargError) as caught:
        entry.open_reader(entry.make_input(tmp_path), **TWO_KWARGS)
    message = str(caught.value)
    for name in TWO_KWARGS:
        assert name in message


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_rejection_states_that_no_reader_kwargs_are_accepted(
    entry: EntryPoint, tmp_path: Path
) -> None:
    """The user needs to know that no other spelling works either."""
    with pytest.raises(SnoutyReaderKwargError) as caught:
        entry.open_reader(entry.make_input(tmp_path), **TILE_SIZE_KWARG)
    assert "accept no reader kwargs" in str(caught.value)


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_rejection_points_at_the_two_environment_variables(
    entry: EntryPoint, tmp_path: Path
) -> None:
    """The env vars are the only supported controls, so the error names them."""
    with pytest.raises(SnoutyReaderKwargError) as caught:
        entry.open_reader(entry.make_input(tmp_path), **TILE_SIZE_KWARG)
    message = str(caught.value)
    assert "ZARRMONY_SNOUTY_MODE" in message
    assert "ZARRMONY_SNOUTY_ENGINE" in message


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_rejection_is_a_zarrmony_reader_kwarg_error(entry: EntryPoint, tmp_path: Path) -> None:
    """The type decides whether the user reads a sentence or a stack trace.

    ``zarrmony.cli`` converts a fixed set of exception types into a
    ``ClickException``. ``ReaderKwargError`` is in that set, and is the type
    zarrmony defines for this situation.
    """
    with pytest.raises(SnoutyReaderKwargError) as caught:
        entry.open_reader(entry.make_input(tmp_path), **TILE_SIZE_KWARG)
    assert isinstance(caught.value, ReaderKwargError)


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_rejection_stays_in_this_package_error_family(entry: EntryPoint, tmp_path: Path) -> None:
    """A library caller that catches ``SnoutyError`` keeps catching it."""
    with pytest.raises(SnoutyError):
        entry.open_reader(entry.make_input(tmp_path), **TILE_SIZE_KWARG)


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_rejects_before_the_reader_is_constructed(
    entry: EntryPoint, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No directory scan, and no file in ``data/`` is opened.

    The reader class is replaced by a callable that fails the test. If the
    error arrives, construction never started.
    """

    def _must_not_run(*args: Any, **kwargs: Any) -> Any:
        pytest.fail(f"{entry.reader_attr} was constructed before the kwarg was rejected")

    monkeypatch.setattr(zarrmony_snouty, entry.reader_attr, _must_not_run)
    with pytest.raises(SnoutyReaderKwargError):
        entry.open_reader(entry.make_input(tmp_path), **TILE_SIZE_KWARG)


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_rejects_without_reading_the_input_at_all(entry: EntryPoint, tmp_path: Path) -> None:
    """A path that does not exist still produces the kwarg error."""
    with pytest.raises(SnoutyReaderKwargError):
        entry.open_reader(tmp_path / "does-not-exist", **TILE_SIZE_KWARG)


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_cli_convert_prints_a_sentence_and_no_traceback(entry: EntryPoint, tmp_path: Path) -> None:
    """The whole point of the error type, checked through the real CLI.

    The reader is reached through the entry point here, not imported, so a
    broken registration fails this test too.
    """
    result = CliRunner().invoke(
        app,
        [
            "convert",
            str(entry.make_input(tmp_path / "input")),
            str(tmp_path / "out.ome.zarr"),
            "--reader-kwarg",
            "tile_size=64,128",
        ],
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "tile_size" in result.output
    assert "ZARRMONY_SNOUTY_MODE" in result.output
    assert not (tmp_path / "out.ome.zarr").exists()
