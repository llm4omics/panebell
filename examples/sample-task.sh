#!/bin/sh
# Run inside a tmux pane on the project host.
set -eu
helper="${PANEBELL_HELPER:-$HOME/.local/lib/panebell/panebell.py}"
id="colour-list-$(python3 -c 'import uuid; print(uuid.uuid4())')"
python3 "$helper" emit waiting "$id" --name 'Paper Planes' --summary 'Check the fictional colour list?'
trap 'python3 "$helper" emit clear "$id" --name "Paper Planes"' EXIT
printf 'Check the list? [y/N] '
read -r answer
case "$answer" in
  y|Y) result=$(printf '%s\n' amber teal teal | sort | uniq -d)
       python3 "$helper" emit complete "$id" --name 'Paper Planes' --summary "Duplicate colour: $result" ;;
esac
