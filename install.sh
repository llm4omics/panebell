#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
dest="${PANEBELL_APPS_DIR:-$HOME/Applications}"
cfg="${PANEBELL_CONFIG:-$HOME/.config/panebell/config.json}"
mkdir -p "$dest" "$(dirname "$cfg")"
stage=$(mktemp -d "$dest/.panebell-install.XXXXXX")
"$root/scripts/build.sh" "$stage"
"$stage/PaneBell.app/Contents/MacOS/PaneBell" --self-test
if [ ! -e "$cfg" ]; then
    (umask 077; set -C; printf '%s\n' '{"sshHost":"","projects":[]}' > "$cfg")
fi
if [ -e "$dest/PaneBell.app" ]; then
    mv "$dest/PaneBell.app" "$stage/previous.app"
    printf 'Previous app saved at %s/previous.app\n' "$stage"
fi
if ! mv "$stage/PaneBell.app" "$dest/PaneBell.app"; then
    if [ -e "$stage/previous.app" ]; then mv "$stage/previous.app" "$dest/PaneBell.app"; fi
    exit 1
fi
if [ ! -e "$stage/previous.app" ]; then rmdir "$stage"; fi
printf 'Installed %s/PaneBell.app\nSettings: %s\n' "$dest" "$cfg"
