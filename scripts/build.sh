#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
out=${1:-"$root/build"}
app="$out/PaneBell.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
swiftc -parse-as-library -O -framework AppKit "$root"/Sources/*.swift -o "$app/Contents/MacOS/PaneBell"
cp "$root/Info.plist" "$app/Contents/Info.plist"
cp "$root/panebell.py" "$app/Contents/Resources/"
codesign --force --sign - --identifier org.panebell.menu "$app"
codesign --verify "$app"
