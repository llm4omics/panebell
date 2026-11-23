#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
mkdir -p "${1:-"$root/build"}"
out=$(CDPATH= cd -- "${1:-"$root/build"}" && pwd)
stage=$(mktemp -d "$out/.panebell-build.XXXXXX")
app="$stage/PaneBell.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
(cd "$root" && swiftc -parse-as-library -O -framework AppKit Sources/*.swift -o "$app/Contents/MacOS/PaneBell")
cp "$root/Info.plist" "$app/Contents/Info.plist"
cp "$root/panebell.py" "$app/Contents/Resources/"
codesign --force --sign - --identifier org.panebell.menu "$app"
codesign --verify "$app"
backup=""
if [ -e "$out/PaneBell.app" ]; then
    backup=$(mktemp -d "${TMPDIR:-/tmp}/panebell-previous-build.XXXXXX")
    mv "$out/PaneBell.app" "$backup/PaneBell.app"
fi
if ! mv "$app" "$out/PaneBell.app"; then
    if [ -n "${backup:-}" ]; then mv "$backup/PaneBell.app" "$out/PaneBell.app"; fi
    exit 1
fi
rmdir "$stage"
