"""Smoke test: confirm the plugin is discoverable through zarrmony's entry-point lookup.

Mirrors what an end user does after ``pip install zarrmony-snouty``:

    from zarrmony.readers.plugin import list_plugins
    [p.name for p in list_plugins()]  # -> [..., 'zarrmony-snouty']

If this test passes in CI (which runs ``uv pip install -e ".[dev]"`` from a
fresh venv), the entry-point declaration in ``pyproject.toml`` is wired up.
"""

from __future__ import annotations

import pytest
from zarrmony.readers.plugin import list_plugins

PLUGIN_NAMES = ["zarrmony-snouty", "zarrmony-snouty-session", "zarrmony-snouty-plate"]


@pytest.mark.parametrize("name", PLUGIN_NAMES)
def test_plugin_registered_via_entry_point(name: str) -> None:
    plugins = {p.name: p for p in list_plugins()}
    assert name in plugins, (
        f"{name} did not appear in list_plugins(); check that "
        '[project.entry-points."zarrmony.readers"] in pyproject.toml is intact '
        "and that the package was installed (pip install -e .)."
    )


@pytest.mark.parametrize("name", PLUGIN_NAMES)
def test_registered_plugin_carries_expected_provenance(name: str) -> None:
    plugins = {p.name: p for p in list_plugins()}
    p = plugins[name]
    assert p.distribution == "zarrmony-snouty"
    assert p.source == "entry_point"
