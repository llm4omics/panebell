#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
"$root/scripts/build.sh"
exec "$root/build/PaneBell.app/Contents/MacOS/PaneBell" --demo
