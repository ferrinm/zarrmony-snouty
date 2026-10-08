"""Tests for the pre-commit hook that keeps internal identifiers out of the repo.

The hook is the only thing standing between a paste from a lab share and a
public commit, so its patterns are worth pinning — particularly the negative
cases. A rule that fires on ordinary prose gets suppressed with
``# allow-internal-path`` until it stops protecting anything.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_no_internal_paths.py"
_spec = importlib.util.spec_from_file_location("check_no_internal_paths", _SCRIPT)
assert _spec is not None and _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
sys.modules["check_no_internal_paths"] = check
_spec.loader.exec_module(check)


def scan_text(tmp_path: Path, text: str, name: str = "doc.md") -> list[str]:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return check.scan(path, check.RULES)


CAUGHT = [
    pytest.param("/Volumes/microscopy-ro/slide.vsi", id="read-only-mount"),
    pytest.param("cp /data/microscopy/export .", id="cluster-share"),
    pytest.param("run_Trial#1234 finished", id="trial-number"),
    pytest.param("scenes: ('label', '20x_AAA_B_CCC_01')", id="scene-name-underscores"),
    pytest.param("scene '20x_AAA_B, CCC, DDD, EEE_01'", id="scene-name-comma-list"),
    pytest.param("40X_AAA_BBB_CCC_02.ome.zarr", id="scene-name-uppercase-mag"),
    pytest.param(
        "schema at https://github.com/calico/example-repo/blob/main/x.tf",
        id="internal-org-url",
    ),
    pytest.param("git@github.com:calico/example-repo.git", id="internal-org-ssh"),
    pytest.param(
        "see https://github.com/example-user/zarrmony for the guide",
        id="private-sibling-via-redirect",
    ),
    pytest.param(
        "https://github.com/example-user/zarrmony/blob/main/docs/x.md",
        id="private-sibling-deep-link",
    ),
    pytest.param(
        "mirrors https://github.com/example-user/zarrmony-blaze/issues/1",
        id="private-sibling-blaze",
    ),
    pytest.param("git@github.com:example-user/zarrmony.git", id="private-sibling-ssh"),
    # The same claim through a host that is not literally `github.com`, and
    # through a path with an extra segment in front of the owner (#59). Both
    # are ordinary shapes: a README embeds a snippet over the raw host, and a
    # script reads metadata over the API host.
    pytest.param(
        "https://raw.githubusercontent.com/example-user/zarrmony/main/docs/x.md",
        id="private-sibling-raw-host",
    ),
    pytest.param(
        "https://api.github.com/repos/example-user/zarrmony",
        id="private-sibling-api-host",
    ),
    pytest.param(
        "https://raw.githubusercontent.com/calico/example-repo/main/x.tf",
        id="internal-org-raw-host",
    ),
    pytest.param(
        "https://api.github.com/repos/calico/example-repo",
        id="internal-org-api-host",
    ),
]


@pytest.mark.parametrize("line", CAUGHT)
def test_offending_lines_are_reported(tmp_path: Path, line: str) -> None:
    assert scan_text(tmp_path, line), f"expected a finding for {line!r}"


ALLOWED = [
    pytest.param("The slide was imaged at 20x on a widefield scanner.", id="bare-magnification"),
    pytest.param("Main scene: 20×, Z=1, T=1, 10 source pyramid levels.", id="prose-magnification"),
    pytest.param("Sharding was 15.2x fewer objects than the chunk grid.", id="ratio"),
    pytest.param("`20x_DAPI` is two tokens and stays under the threshold.", id="two-tokens"),
    pytest.param("store = f'{OUT}/slide-B/<main-scene>.ome.zarr'", id="placeholder"),
    pytest.param("Set $SRC to the reference dataset's path.", id="env-placeholder"),
    pytest.param("metadata_<dataset>.json", id="dataset-placeholder"),
    pytest.param(
        "owned by https://github.com/calicolabs/example-backend, which ingests.",
        id="public-calicolabs-org",
    ),
    pytest.param(
        "The source of truth is `infra-repo/deploy/arch/bigquery.tf`.",
        id="bare-internal-repo-path",
    ),
    pytest.param("Reviewers are `@calico/sweng-dev`.", id="codeowners-team"),
    pytest.param(
        "https://github.com/example-user/zarrmony-snouty/blob/main/README.md",
        id="this-repo-is-public",
    ),
    pytest.param("Install it from https://pypi.org/project/zarrmony/.", id="pypi-project"),
    pytest.param("The plugin entry point group is `zarrmony.readers`.", id="dotted-name"),
    # The looser host and the extra path segments must not cost the negative
    # cases. This repo is public over every host, and the public org keeps
    # its own name under the raw host too.
    pytest.param(
        "https://raw.githubusercontent.com/example-user/zarrmony-snouty/main/README.md",
        id="this-repo-raw-host",
    ),
    pytest.param(
        "https://api.github.com/repos/example-user/zarrmony-snouty",
        id="this-repo-api-host",
    ),
    pytest.param(
        "https://raw.githubusercontent.com/calicolabs/example-backend/main/x.py",
        id="public-calicolabs-raw-host",
    ),
]


@pytest.mark.parametrize("line", ALLOWED)
def test_ordinary_text_is_not_flagged(tmp_path: Path, line: str) -> None:
    assert scan_text(tmp_path, line) == []


def test_allow_marker_suppresses_a_line(tmp_path: Path) -> None:
    text = f"/data/microscopy/export  # {check.ALLOW_MARKER}"
    assert scan_text(tmp_path, text) == []


def test_unscanned_suffixes_are_skipped(tmp_path: Path) -> None:
    assert scan_text(tmp_path, "/data/microscopy/export", name="notes.rst") == []


# The checker, its tests and the convention doc quote the patterns they exist
# to catch, so every rule fires on them. The skip list lives in the checker so
# that CI and pre-commit agree about it (#59). Before that, only the
# pre-commit config knew, and `check_no_internal_paths.py $(git ls-files)`
# failed on a clean tree.
SELF_DESCRIBING_FILES = [
    pytest.param("scripts/check_no_internal_paths.py", id="the-checker"),
    pytest.param("tests/test_check_no_internal_paths.py", id="its-tests"),
    pytest.param("CONTRIBUTING.md", id="the-convention-doc"),
]


@pytest.mark.parametrize("relative", SELF_DESCRIBING_FILES)
def test_self_describing_files_are_skipped(relative: str) -> None:
    path = Path(__file__).resolve().parent.parent / relative
    assert path.exists(), f"{relative} moved; update the skip list in the checker"
    assert check.scan(path, check.RULES) == []


def test_the_tracked_tree_is_clean() -> None:
    """Every tracked file passes. This is what the CI step asserts (#59)."""
    root = Path(__file__).resolve().parent.parent
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if listing.returncode != 0:
        pytest.skip("not a git checkout")

    rules = check.RULES  # Not load_rules(): .internal-patterns is site-local.
    problems: list[str] = []
    for name in listing.stdout.split("\0"):
        if name:
            problems.extend(check.scan(root / name, rules))
    assert problems == []
