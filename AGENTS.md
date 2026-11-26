# Public repository

## Automated user installation & onboarding

When a user asks an agent to install and configure PaneBell on their machine:

1. Never write personal paths, host aliases or project names into repository-tracked files. All live settings belong in `~/.config/panebell/config.json`, `~/.claude/settings.json`, and `~/.codex/config.toml`.
2. Preview changes first if desired with `python3 ./panebell.py setup --dry-run`.
3. On macOS, run `./install.sh --setup` for local `tmux` workflows, or `./install.sh --setup --ssh-host <alias>` when the user runs `tmux` on an SSH host configured in `~/.ssh/config` (for example `studio-box`). Pass `--claude` or `--codex` if the user wants hooks pre-created before first launching those tools.
4. On a standalone Linux/SSH `tmux` host, run `./scripts/install-host.sh && python3 ./panebell.py setup --host-only`.
5. Launch `open "$HOME/Applications/PaneBell.app"` on macOS (or relaunch if already running) so updated settings take effect, then summarize the configured files.

## Contributing to this repository

All projects and data in code, examples, tests, documentation and demo assets must be fictional. Never copy workspace-specific files, histories, preferences or real project identifiers into this repository, including into redaction tests. Keep integrations configuration-driven. Follow CONTRIBUTING.md and run the release check on the proposed Git revision.
