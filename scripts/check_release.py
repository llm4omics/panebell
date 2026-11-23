#!/usr/bin/env python3
"""Check the reviewed public inventory in a Git tree, independent of the worktree."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


PUBLIC = set("""
.github/workflows/ci.yml .gitignore AGENTS.md CONTRIBUTING.md Info.plist LICENSE README.md README.zh-CN.md
Sources/Menu.swift Sources/Models.swift demo.sh docs/setup.md examples/config.json
examples/sample-task.sh examples/wezterm-focus.lua install.sh panebell.py scripts/build.sh scripts/check_release.py
scripts/install-host.sh tests/test_panebell.py
""".split())
PATTERNS = {
    "named home directory": r"/(?:Users|home)/[A-Za-z0-9_][\w.-]*/",
    "machine-specific mounted volume": r"/(?:Volumes|mnt|media)/[\w.-]+/",
    "private network address": r"\b(?:10|127)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|\b192\.168\.\d+\.\d+\b|\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+\b",
    "private key or access token": r"-----BEGIN (?:OPENSSH|RSA|EC|DSA|ENCRYPTED) PRIVATE KEY-----|\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{16,}\b",
    "personal email": r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}",
}


def git(*args):
    return subprocess.check_output(["git", *args])


def check(ref, require_all=True):
    tree = git("rev-parse", "--verify", ref+"^{tree}").decode().strip()
    records = git("ls-tree", "-rz", tree).split(b"\0")
    seen, errors = set(), []
    for record in filter(None, records):
        stat, name = record.split(b"\t", 1)
        mode, kind, oid = stat.decode().split()
        name = name.decode()
        seen.add(name)
        if name not in PUBLIC or mode not in ("100644", "100755") or kind != "blob":
            errors.append(name+": unreviewed release entry")
            continue
        blob = git("cat-file", "blob", oid)
        try:
            body = blob.decode()
            if "\0" in body or len(blob) > 256000:
                raise ValueError()
        except (UnicodeError, ValueError):
            errors.append(name+": expected reviewed text")
            continue
        for label, expr in PATTERNS.items():
            if re.search(expr, body):
                errors.append(name+": "+label)
        if name == "examples/config.json":
            example = json.loads(body)
            if example.get("sshHost") or [(p["name"],p["path"]) for p in example["projects"]] != [
                    ("Paper Planes","~/Sandbox/paper-planes"),("Lantern Atlas","~/Sandbox/lantern-atlas")]:
                errors.append(name+": expected fictional demo settings")
    if require_all:
        errors.extend("missing: "+name for name in sorted(PUBLIC-seen))
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ref", nargs="?", default="HEAD")
    parser.add_argument("--history", action="store_true", help="Also inspect all reachable commits")
    args = parser.parse_args()
    errors = check(args.ref)
    if args.history:
        commits = git("rev-list", args.ref).decode().splitlines()
        for commit in commits:
            errors.extend(commit[:10]+": "+error for error in check(commit, require_all=False))
            message = git("show", "-s", "--format=%B", commit).decode()
            errors.extend(commit[:10]+": "+label for label, expr in PATTERNS.items() if re.search(expr, message))
    if errors:
        sys.exit("\n".join(errors))
    print("Public release inventory verified:", len(PUBLIC), "files")
