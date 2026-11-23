# PaneBell

[简体中文](README.zh-CN.md)

PaneBell puts your tmux projects and their requests for attention in the macOS menu bar. Click a project to return to its terminal. When a run finishes while your Mac is away, its result is still there when you reconnect.

## Try the demo

On a Mac with Xcode Command Line Tools, run `./demo.sh` from this checkout. Click **PaneBell Demo · 1 → Waiting for you → Paper Planes**. A small project window asks permission to check three colours. Click **Allow check**, close the window, then open **Unread completions**. The count stays until you mark the result as read. The demo uses memory only and requires no server, tmux session, model account or SSH key.

## Install and open a project

You need macOS 13 or later, Xcode Command Line Tools and Python 3.9+. Install tmux on the machine where the projects run. Terminal.app is the default. WezTerm and VS Code are optional.

1. Run `./install.sh`, then `open "$HOME/Applications/PaneBell.app"`. macOS may ask permission to show notifications and control Terminal.
2. Existing windows appear under **Live windows**. To define an on-demand project, choose **Open settings**, add its name, tmux session and working directory, and relaunch the app. First install creates an empty list. [Example settings](examples/config.json)
3. Open a project from **Projects**. PaneBell reuses a matching window or creates a shell in the configured directory. If several panes match, you choose. **Pin to menu** keeps a shortcut. **Recently opened** holds eight projects you deliberately opened from the menu.
4. Choose **Open side by side** for a separate terminal view. **Copy project path**, **Open in VS Code** and, for local paths, **Reveal in Finder** use the selected project.

A saved project follows its directory, including subdirectories. A live window follows its specific pane even after changing directories. Each Mac uses its own tmux view so opening a window need not switch another client’s active window.

## Send a real approval or completion

Run `./scripts/install-host.sh` on the project host. Inside a tmux pane, run `sh examples/sample-task.sh`. Select the waiting event in the menu, open the original window and type `y`. The task checks its fictional list, reports the duplicate colour and leaves a durable completion.

For your own job, assign one ID per run and emit transitions at the points the job actually reaches. A successful exit or verified result is a good place for `complete`. Use `clear` when a request is cancelled or handled. An idle shell or a closed window cannot establish that a run succeeded.

```sh
helper="$HOME/.local/lib/panebell/panebell.py"
python3 "$helper" emit waiting palette-001 --name "Paper Planes" --summary "Check the colour list?"
# After the job has actually finished:
python3 "$helper" emit complete palette-001 --name "Paper Planes" --summary "One duplicate colour."
```

Repeated delivery of the same completion is harmless. Closing a waiting pane hides its outstanding request. An explicit completion remains readable after its pane closes. Marking a cached result as read while offline syncs the acknowledgement on reconnect. Event summaries are stored on the project host and cached in the Mac app’s preferences.

See [configuration and integrations](docs/setup.md) for SSH, WezTerm, project launch commands, Codex completion notifications, automatic Claude Code permission reminders and working with other task runners.

## Verify the local build

Run these commands on the Mac from the extracted source directory.

```sh
./scripts/build.sh
./build/PaneBell.app/Contents/MacOS/PaneBell --self-test
```

This source release builds an ad hoc signed application locally. [MIT license](LICENSE)
