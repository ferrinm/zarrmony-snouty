"""The sdist ships an allow-list, not everything that a VCS does not ignore.

A maintainer working tree holds untracked state that `.gitignore` does not
name: `.claude/`, which carries `settings.local.json` and whole worktree
copies of the repository, and `.envrc`. hatchling keeps every file a VCS does
not ignore, so the deny-list that `pyproject.toml` used to carry shipped each
new untracked directory until somebody noticed (#58). PyPI stayed clean only
because `.github/workflows/release.yml` builds from a fresh checkout.

These tests build from a copy of the tracked tree with that untracked state
planted back in. The copy keeps the answer the same on every host. A fresh CI
checkout has no `.claude/`, so a test against the real working tree would
pass there even with the defect present.
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
from hatchling.builders.sdist import SdistBuilder

REPO_ROOT = Path(__file__).resolve().parent.parent

# Untracked state that a maintainer tree holds and `.gitignore` does not name.
# `.claude/sessions/` and `.claude/scheduled_tasks.json` are ignored; the rest
# of `.claude/` is not, which is how three repository copies reached a local
# sdist.
PLANTED = {
    ".claude/settings.local.json": '{"permissions": {}}\n',
    ".claude/worktrees/issue-1/pyproject.toml": '[project]\nname = "copy"\n',
    ".claude/worktrees/issue-1/src/zarrmony_snouty/__init__.py": "",
    ".envrc": "layout_uv\n",
}

# What `[tool.hatch.build.targets.sdist]` must produce, plus the `PKG-INFO`
# that hatchling writes. This is the content of the published 0.3.2 sdist.
# Change this set and the allow-list together.
EXPECTED_TOP_LEVEL = frozenset(
    {
        "PKG-INFO",
        ".github",
        ".gitignore",
        ".pre-commit-config.yaml",
        "CHANGELOG.md",
        "CLAUDE.md",
        "CONTEXT.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "README.md",
        "pyproject.toml",
        "scripts",
        "src",
        "tests",
        "uv.lock",
    }
)


def _copy_tracked_tree(destination: Path) -> None:
    """Copy every tracked file into ``destination``, keeping the layout."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if listing.returncode != 0:
        pytest.skip("not a git checkout")

    for name in listing.stdout.split("\0"):
        source = REPO_ROOT / name
        if not name or not source.is_file():
            continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


@pytest.fixture(scope="module")
def sdist_members(tmp_path_factory: pytest.TempPathFactory) -> frozenset[str]:
    """Paths inside an sdist built from a planted copy, without the name prefix."""
    base = tmp_path_factory.mktemp("sdist")
    source = base / "tree"
    source.mkdir()
    _copy_tracked_tree(source)

    for name, text in PLANTED.items():
        planted = source / name
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_text(text, encoding="utf-8")

    output = base / "dist"
    artifacts = list(SdistBuilder(str(source)).build(directory=str(output)))
    assert len(artifacts) == 1, f"expected one sdist, got {artifacts}"

    with tarfile.open(artifacts[0]) as tar:
        names = tar.getnames()

    # Every member sits under a single `<name>-<version>/` directory.
    return frozenset(name.split("/", 1)[1] for name in names if "/" in name)


@pytest.mark.parametrize("planted", sorted(PLANTED))
def test_untracked_maintainer_state_stays_out(sdist_members: frozenset[str], planted: str) -> None:
    assert planted not in sdist_members


def test_no_member_sits_under_dot_claude(sdist_members: frozenset[str]) -> None:
    """The regression from #58, stated the way the issue states it."""
    assert [name for name in sdist_members if ".claude" in name] == []


def test_top_level_matches_the_allow_list(sdist_members: frozenset[str]) -> None:
    top_level = {name.split("/", 1)[0] for name in sdist_members}
    assert top_level == set(EXPECTED_TOP_LEVEL)


def test_documentation_and_triage_notes_stay_out(sdist_members: frozenset[str]) -> None:
    """The old deny-list kept these two out. The allow-list must keep doing it."""
    assert [name for name in sdist_members if name.startswith(("docs/", ".out-of-scope"))] == []


def test_the_package_and_its_tests_ship(sdist_members: frozenset[str]) -> None:
    assert "src/zarrmony_snouty/__init__.py" in sdist_members
    assert "tests/conftest.py" in sdist_members
    assert "scripts/check_no_internal_paths.py" in sdist_members
