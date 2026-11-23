#!/usr/bin/env python3
"""Check the reviewed public inventory in a Git tree, independent of the worktree."""
import argparse
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path
import re
import subprocess
import sys


PUBLIC = set("""
.gitattributes .github/workflows/ci.yml .gitignore AGENTS.md CONTRIBUTING.md Info.plist LICENSE README.md README.zh-CN.md
Sources/Menu.swift Sources/Models.swift demo.sh docs/setup.md examples/claude-hooks.json examples/config.json
examples/sample-task.sh examples/wezterm-focus.lua install.sh panebell.py scripts/build.sh scripts/check_release.py
scripts/install-host.sh tests/test_panebell.py
""".split())
DISTRIBUTION = set("""
LICENSE README.md README.zh-CN.md Info.plist Sources/Menu.swift Sources/Models.swift
demo.sh docs/setup.md examples/claude-hooks.json examples/config.json examples/sample-task.sh
examples/wezterm-focus.lua install.sh panebell.py scripts/build.sh scripts/install-host.sh
""".split())
RELEASE_IDENTITY = ("PaneBell contributors", "maintainers" + "@panebell.invalid")
PATTERNS = {
    "named home directory": r"(?:/(?:Users|home)/|[A-Za-z]:[\\/]Users[\\/])[\w.-]+(?:[/\\]|\b)",
    "machine-specific mounted volume": r"/(?:Volumes|mnt|media)/[\w.-]+/",
    "private network address": r"\b(?:10|127)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|\b172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+\b|\b192\.168\.\d+\.\d+\b|\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+\b",
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
        executable = name.endswith(".sh") or name in {"panebell.py", "scripts/check_release.py"}
        if mode != ("100755" if executable else "100644"):
            errors.append(name+": incorrect release file permissions")
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


def release_bytes(ref):
    """Encode only reviewed Git blobs; fixed metadata makes even appended bytes detectable."""
    tree = git("rev-parse", "--verify", ref+"^{tree}").decode().strip()
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(DISTRIBUTION):
            record = git("ls-tree", tree, "--", name).split(b"\t")[0].split()
            mode, kind, oid = record
            if mode not in (b"100644", b"100755") or kind != b"blob":
                raise ValueError("Unexpected distribution entry: " + name)
            info = zipfile.ZipInfo("PaneBell/" + name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = int(mode, 8) << 16
            archive.writestr(info, git("cat-file", "blob", oid))
    return result.getvalue()


def write_archive(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
        out.write(content)
    os.replace(out.name, path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ref", nargs="?", default="HEAD")
    parser.add_argument("--history", action="store_true", help="Also inspect all reachable commits")
    parser.add_argument("--write-zip", type=Path, help="Write a reproducible user source release")
    parser.add_argument("--verify-zip", type=Path, help="Verify the complete release ZIP, including its metadata")
    args = parser.parse_args()
    errors = check(args.ref)
    if args.history:
        commits = git("rev-list", args.ref).decode().splitlines()
        for commit in commits:
            errors.extend(commit[:10]+": "+error for error in check(commit, require_all=False))
            identities = git("show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce", commit).decode().rstrip("\n").split("\0")
            if tuple(identities) != RELEASE_IDENTITY * 2:
                errors.append(commit[:10]+": release history has an unreviewed author or committer")
            message = git("show", "-s", "--format=%B", commit).decode()
            errors.extend(commit[:10]+": "+label for label, expr in PATTERNS.items() if re.search(expr, message))
    if errors:
        sys.exit("\n".join(errors))
    if args.write_zip or args.verify_zip:
        expected = release_bytes(args.ref)
        if args.write_zip:
            write_archive(args.write_zip, expected)
        if args.verify_zip and args.verify_zip.read_bytes() != expected:
            sys.exit("ZIP differs from canonical distribution bytes or metadata")
        print("User distribution verified:", len(DISTRIBUTION), "files")
    print("Public release inventory verified:", len(PUBLIC), "files")
