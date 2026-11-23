#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
dest="$HOME/.local/lib/panebell"
mkdir -p "$dest"
tmp=$(mktemp "$dest/.install.XXXXXX")
install -m 755 "$root/panebell.py" "$tmp"
mv "$tmp" "$dest/panebell.py"
printf 'Installed %s/panebell.py\n' "$dest"
