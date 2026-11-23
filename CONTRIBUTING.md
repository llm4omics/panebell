# Contributing

Use invented projects, sample data and host aliases in every change. This includes source, tests, documentation, demonstrations, screenshots, issue examples, CI logs and release archives. Keep actual user configuration and receipts outside the checkout.

Use the in-memory demo for any published visual. Before publishing, verify the exact Git revision with `python3 scripts/check_release.py HEAD --history` and inspect every newly tracked file and its history. Add new public deliverables to the release inventory only after reviewing their content. Do not include production exports, terminal captures or application preferences.

## Release procedure

Run `python3 -B -m unittest discover -s tests -v` with tmux installed, then build and self-test the app on macOS. Use the neutral project identity for commits intended for the anonymized release branch.

```sh
python3 scripts/check_release.py HEAD --history --write-zip build/PaneBell-source.zip
python3 scripts/check_release.py HEAD --history --verify-zip build/PaneBell-source.zip
```

Only the end-user source inventory belongs in the ZIP. CI, contributor instructions, tests and the release checker stay in the repository. Keep Git archive export attributes in sync with that inventory.
