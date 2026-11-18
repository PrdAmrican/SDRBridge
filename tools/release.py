#!/usr/bin/env python3
# SDRBridge - Copyright (C) 2026 PrdAmrican and SDRBridge contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""
release.py - cut a new SDRBridge version.

    python3 tools/release.py patch      # 1.0.0 -> 1.0.1
    python3 tools/release.py minor      # 1.0.0 -> 1.1.0
    python3 tools/release.py major      # 1.0.0 -> 2.0.0
    python3 tools/release.py 1.2.3      # explicit
    add --push to push the commit and tag to GitHub

It updates __version__ in nrsp_bridge.py (the only place the version is
stored), moves the "Unreleased" notes in CHANGELOG.md under the new version,
runs the tests, commits and creates the tag vX.Y.Z.
"""
import argparse
import datetime
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "nrsp_bridge.py")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
VER_RE = re.compile(r'^__version__ = "(\d+)\.(\d+)\.(\d+)"$', re.M)


def git(*a, check=True):
    return subprocess.run(["git", *a], cwd=ROOT, check=check, text=True, capture_output=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bump", help="major | minor | patch | X.Y.Z")
    ap.add_argument("--push", action="store_true", help="push commit and tag to origin")
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()

    if git("status", "--porcelain"):
        sys.exit("working tree has uncommitted changes; commit or stash them first")

    src = open(SRC).read()
    m = VER_RE.search(src)
    if not m:
        sys.exit("could not find __version__ in nrsp_bridge.py")
    major, minor, patch = map(int, m.groups())
    if a.bump == "major":
        new = f"{major + 1}.0.0"
    elif a.bump == "minor":
        new = f"{major}.{minor + 1}.0"
    elif a.bump == "patch":
        new = f"{major}.{minor}.{patch + 1}"
    elif re.fullmatch(r"\d+\.\d+\.\d+", a.bump):
        new = a.bump
    else:
        sys.exit("bump must be major, minor, patch or X.Y.Z")
    tag = f"v{new}"
    if git("tag", "--list", tag):
        sys.exit(f"tag {tag} already exists")

    log = open(CHANGELOG).read()
    m2 = re.search(r"## \[Unreleased\]\n(.*?)(?=\n## \[|\Z)", log, re.S)
    notes = m2.group(1).strip() if m2 else ""
    if not notes:
        sys.exit("CHANGELOG.md has nothing under [Unreleased]; describe the changes there first")

    if not a.skip_tests:
        print("running tests...")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "test_bridge.py")], cwd=ROOT)
        if r.returncode:
            sys.exit("tests failed; not releasing")

    open(SRC, "w").write(VER_RE.sub(f'__version__ = "{new}"', src, count=1))
    today = datetime.date.today().isoformat()
    log = log.replace(m2.group(0), f"## [Unreleased]\n\n## [{new}] - {today}\n\n{notes}\n", 1)
    open(CHANGELOG, "w").write(log)

    git("add", SRC, CHANGELOG)
    git("commit", "-m", f"Release {tag}")
    git("tag", "-a", tag, "-m", f"SDRBridge {tag}\n\n{notes}")
    print(f"released {tag}")
    if a.push:
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        subprocess.run(["git", "push", "origin", branch], cwd=ROOT, check=True)
        subprocess.run(["git", "push", "origin", tag], cwd=ROOT, check=True)
    else:
        print(f"push with: git push origin HEAD && git push origin {tag}")


if __name__ == "__main__":
    main()
