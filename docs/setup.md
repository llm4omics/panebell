# Settings and integrations

The Mac reads `~/.config/panebell/config.json` when the app starts. A project has a display name, a tmux session name and an absolute or home-relative directory on the project host. Directory names may contain spaces. A missing directory produces an error. The private configuration is outside the checkout. [Fictional configuration](../examples/config.json)

## Remote project host

1. Configure an SSH alias in your own SSH config. Check it with `ssh studio-box true`, replacing the fictional alias. Set up key authentication on the Mac.
2. On the host, install tmux and Python 3.9+ and run `./scripts/install-host.sh` from a checkout. Alternatively copy only `panebell.py` to `~/.local/lib/panebell/panebell.py`. To install elsewhere, set `remoteHelper` in the Mac settings to the absolute path of the helper on the host. SSH must be able to find `python3` and that account’s tmux server.
3. Set `"sshHost": "studio-box"` on the Mac and relaunch. Clearing this field restores local operation. SSH transports both the status requests and the interactive terminal. On a failed project-open connection, **Log in and retry** opens a terminal where you can unlock your key. The pending project is retried after a successful login.

To use WezTerm for projects, set `"terminal": "wezterm"`. The default is `"terminal"`. SSH unlock uses Terminal.app. VS Code remote opening requires the Remote - SSH extension and an absolute remote path. Finder opens local paths.

## Launch something other than a shell

The host account can define `~/.config/panebell/recipes.json`. An entry applies when creating a new window at the matching session and directory. Existing windows continue their original jobs. Supply executable arguments as strings.

```json
[
  {
    "session": "sketchbook",
    "path": "~/Sandbox/paper-planes",
    "command": ["python3", "-i"]
  }
]
```

## Connect an AI or batch runner

The `emit` CLI accepts `waiting`, `clear` and `complete`. Use a distinct run ID for every new job. Call it inside the job's tmux pane to capture its identity. A noninteractive runner can pass `--session` and `--path` to offer a project link. Without either, the event still has a readable summary. An event requesting permission takes you to the runner. Grant or deny the request in that runner.

Codex CLI users can opt into durable turn-completion receipts through its external notification command. Point `notify` in your own Codex configuration at Python and the installed helper followed by `codex-notify`. Supply the actual absolute helper path since an argument array does not expand shell variables. Load the configuration in a new Codex session running inside tmux. The adapter consumes an explicit `agent-turn-complete` notification and deduplicates by thread and turn ID. It does not infer an approval request from reply text. Call `emit waiting` from an appropriate approval hook if you want waiting events as well. If your setup already defines a notification program, have that program forward the same event to PaneBell.

## Storage and removal

The host receipt database lives at `~/.local/state/panebell/events.sqlite3`. The Mac uses the app’s preferences for saved projects, cached summaries and offline acknowledgements. No HTTP listener, external account or model call is required. Use your normal macOS Login Items settings to start the app when you sign in.

Quit PaneBell before updating or removing it. Run the installer again to update. To uninstall, move PaneBell.app to the Trash and remove it from Login Items if you added it. Keep your private settings and receipt database for reinstallation, or remove them yourself when no longer needed.

## Record deliberate WezTerm switches

To include manual switches between PaneBell's primary WezTerm project views in **Recently opened**, copy [the optional module](../examples/wezterm-focus.lua) into your WezTerm configuration directory and load it with `require("wezterm-focus")` from your existing configuration. It notifies the running app on foreground changes. The app confirms the active tmux pane before recording it. Background title changes and side-by-side views do not affect this list.
