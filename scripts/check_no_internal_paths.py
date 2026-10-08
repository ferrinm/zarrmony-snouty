#!/usr/bin/env python3
"""Block internal share paths and dataset identifiers from reaching a public commit.

zarrmony is a public repo; the datasets it is tested against are not. Absolute
paths to lab shares, sample/accession IDs and collaborator names leak
experimental design (what is being stained, in what model, for whom) even when
the surrounding text is purely technical.

Run as a pre-commit hook over staged files. Use placeholders instead:
``/mnt/readonly/<dataset>``, ``$SRC``, ``metadata_<dataset>.json``,
``<main-scene>``.

Add a trailing ``# allow-internal-path`` comment on a line that is a genuine
false positive. Keep those rare — the point is that the reviewer has to look.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ALLOW_MARKER = "allow-internal-path"

REPO_ROOT = Path(__file__).resolve().parent.parent

# These three files quote the patterns they exist to catch, so the rules below
# fire on them. The list lives here rather than in `.pre-commit-config.yaml`
# because two callers need it: the hook, and the CI step that runs the checker
# over `git ls-files` (#59). One of them knowing is how a clean tree failed.
SELF_DESCRIBING = frozenset(
    {
        Path("scripts/check_no_internal_paths.py"),
        Path("tests/test_check_no_internal_paths.py"),
        Path("CONTRIBUTING.md"),
    }
)

# The hosts that serve a GitHub repository. `github.com` covers the web UI,
# the SSH remote and `api.github.com`; `githubusercontent.com` covers
# `raw.githubusercontent.com`. A rule that demanded the literal `github.com`
# missed the raw host, which is the normal way a README embeds a snippet from
# another repository (#59).
GITHUB_HOST = r"github(?:usercontent)?\.com[/:]"

# Path segments between the host and the name being matched. There can be
# none (`raw.githubusercontent.com/<owner>/`), one (`github.com/<owner>/`) or
# two (`api.github.com/repos/<owner>/`), and an unseen shape must not get a
# free pass, so the count is open. The segment class excludes `/`, so each
# repetition consumes exactly one segment and the match cannot blow up.
PATH_SEGMENTS = r"(?:[A-Za-z0-9_.-]+/)*"

# Patterns kept here are *structural* — they describe the shape of an internal
# path, never the name of a specific lab, collaborator or study. A blocklist
# naming the things it protects would publish them itself, which is exactly the
# disclosure this hook exists to prevent.
#
# Each entry is (compiled pattern, what to write instead).
RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"/Volumes/[A-Za-z0-9._-]*-ro\b"),
        "internal read-only mount; use /mnt/readonly/<dataset>",
    ),
    (
        re.compile(r"/data/microscopy/"),
        "internal cluster share; use $SRC or /mnt/readonly/<dataset>",
    ),
    (
        # No leading \b: these show up as `..._Trial#1234`, and `_` is a word
        # character, so a word boundary would never match there.
        re.compile(r"Trial[#_ ]?\d{3,}\b", re.IGNORECASE),
        "trial number identifying a specific experiment",
    ),
    (
        # Links into the internal GitHub org. Naming the org here is safe: the
        # company name is already public in LICENSE, and the README links the
        # separate public `calicolabs` org. What leaks is the repo under it, so
        # the trailing slash is required — it keeps `calicolabs/` from matching.
        re.compile(GITHUB_HOST + PATH_SEGMENTS + r"calico/", re.IGNORECASE),
        "link into the internal GitHub org; name the file, drop the URL",
    ),
    (
        # The same leak through the other spelling. `calico/zarrmony` and
        # `calico/zarrmony-blaze` are private, and GitHub redirects a personal
        # fork path onto them, so a `<user>/zarrmony` link reads as public and
        # resolves to an internal repo. The rule keys on the repo name rather
        # than the owner for that reason.
        #
        # Naming them here publishes nothing: both are public PyPI packages,
        # and `zarrmony` is this package's own dependency in pyproject.toml.
        # What the URL adds is the claim that a repo by that name is readable,
        # which is false for every reader outside the org.
        #
        # The negative lookahead keeps `zarrmony-snouty` out. That repo is
        # genuinely public and links to it are correct.
        re.compile(
            GITHUB_HOST + PATH_SEGMENTS + r"zarrmony(?:-blaze)?(?![\w-])",
            re.IGNORECASE,
        ),
        "link to a private sibling repo; name the file, or link the PyPI project",
    ),
    (
        # Slide-scanner scene names: a magnification bolted straight onto the
        # filter panel and an acquisition index, `20x_A_B_C_01`. Structural,
        # not a blocklist — what it keys on is the `<mag>x_` prefix followed by
        # three or more joined tokens. Prose does not have that shape: "imaged
        # at 20x" has no underscore, and `20x_DAPI` is only two tokens, so the
        # `{2,}` keeps the rule off anything short enough to be a variable name.
        re.compile(r"\b\d{1,3}x_[A-Za-z0-9]+(?:[\s,_-]+[A-Za-z0-9]+){2,}", re.IGNORECASE),
        "slide-scanner scene name; use <main-scene>",
    ),
]

# Site-specific names (lab, collaborator, instrument share) belong in an
# untracked file so they never reach the public repo. One regex per line;
# blank lines and `#` comments ignored. Absent by default — see CONTRIBUTING.md.
LOCAL_PATTERNS = Path(__file__).resolve().parent.parent / ".internal-patterns"


def load_rules() -> list[tuple[re.Pattern[str], str]]:
    """``RULES`` plus any site-local patterns, if the untracked file exists."""
    rules = list(RULES)
    if not LOCAL_PATTERNS.exists():
        return rules
    for lineno, raw in enumerate(LOCAL_PATTERNS.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rules.append((re.compile(line, re.IGNORECASE), "site-local identifier"))
        except re.error as exc:
            print(
                f"warning: {LOCAL_PATTERNS.name}:{lineno}: bad regex ({exc}); skipped",
                file=sys.stderr,
            )
    return rules


# What this cannot catch: a bare dataset name carrying a sample ID and a marker
# panel (`<line>_<marker>-<marker>_<marker>`) with no path, trial number or
# magnification around it. There is no safe general pattern for that — a
# biology-term blocklist is unbounded and would fire on legitimate channel
# names. The scene-name rule above only reaches the subset that a slide scanner
# prefixes with a magnification, which is a shape rather than a vocabulary. Add
# the rest to the local pattern file; CONTRIBUTING.md and review cover it.

# File suffixes worth scanning. Everything else (lockfiles, images, binaries)
# is skipped — uv.lock in particular is huge and full of hashes.
SCANNED_SUFFIXES = {
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".org",
    ".cfg",
    ".ini",
    ".sh",
    ".xml",
}


def is_self_describing(path: Path) -> bool:
    """True if ``path`` is one of the files that quote the rules themselves."""
    try:
        relative = path.resolve().relative_to(REPO_ROOT)
    except (OSError, ValueError):
        return False
    return relative in SELF_DESCRIBING


def scan(path: Path, rules: list[tuple[re.Pattern[str], str]]) -> list[str]:
    """Return one message per offending line in ``path``."""
    if path.suffix.lower() not in SCANNED_SUFFIXES:
        return []
    if is_self_describing(path):
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    problems: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        for pattern, advice in rules:
            match = pattern.search(line)
            if match:
                problems.append(f"{path}:{lineno}: {match.group(0)!r} — {advice}")
                break
    return problems


def main(argv: list[str]) -> int:
    rules = load_rules()
    problems: list[str] = []
    for name in argv:
        problems.extend(scan(Path(name), rules))

    if not problems:
        return 0

    print("Internal dataset paths or identifiers found in staged changes:\n")
    for problem in problems:
        print(f"  {problem}")
    print(
        "\nThis repo is public. Replace these with placeholders, or append "
        f"'# {ALLOW_MARKER}' to the line if it is genuinely safe."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
