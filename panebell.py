#!/usr/bin/env python3
"""Local tmux controller and event inbox. The same file runs over SSH."""
import argparse
from contextlib import closing, contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import tempfile

BASE = Path(os.environ.get("PANEBELL_STATE_DIR") or Path.home() / ".local/state/panebell")
CONFIG = Path(os.environ.get("PANEBELL_CONFIG") or Path.home() / ".config/panebell/config.json")
REMOTE = 'python3 "$HOME/.local/lib/panebell/panebell.py"'
SSH = ["/usr/bin/ssh", "-o", "ConnectTimeout=6", "-o", "ServerAliveInterval=8", "-o", "ServerAliveCountMax=2"]
SESSION = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,79}")
PANE = re.compile(r"\d+:%\d+:\d+")
VIEW = "pb-view-"


def config():
    with CONFIG.open() as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a JSON object")
    return data


def host(data):
    alias = data.get("sshHost", "")
    if not isinstance(alias, str) or (alias and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", alias)):
        raise ValueError("sshHost must be an SSH config alias")
    return alias


def remote_command(settings):
    custom = settings.get("remoteHelper")
    if custom is None:
        return REMOTE
    if not isinstance(custom, str) or not custom.startswith("/") or any(ord(c) < 32 for c in custom):
        raise ValueError("remoteHelper must be an absolute path on the SSH host")
    return "python3 " + shlex.quote(custom)


def tmux(*args, check=True):
    binary = shutil.which("tmux")
    if not binary:
        raise ValueError("Install tmux on the project host and ensure it is on PATH")
    proc = subprocess.run([binary, *args], capture_output=True, text=True, timeout=8)
    if check and proc.returncode:
        raise ValueError(proc.stderr.strip() or "tmux command failed")
    return proc


def session(value):
    if not isinstance(value, str) or not SESSION.fullmatch(value) or value.startswith(VIEW):
        raise ValueError("Invalid project session")
    return value


def directory(raw):
    if not isinstance(raw, str) or not raw or any(ord(c) < 32 for c in raw):
        raise ValueError("Invalid project directory")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise ValueError("Use an absolute path or ~/directory")
    return p.resolve()


def panes():
    fields = "#{session_name}\t#{window_index}\t#{pane_index}\t#{pid}:#{pane_id}:#{pane_pid}\t#{pane_dead}\t#{window_name}\t#{pane_current_path}"
    res = tmux("list-panes", "-a", "-F", fields, check=False)
    if res.returncode:
        if "no server running" in res.stderr or "No such file or directory" in res.stderr:
            return []
        raise ValueError(res.stderr.strip())
    result = []
    for line in res.stdout.splitlines():
        row = line.split("\t", 6)
        if len(row) != 7:
            continue
        group, win, pane, key, dead, name, path = row
        if group.startswith(VIEW) or not SESSION.fullmatch(group) or dead != "0" or not PANE.fullmatch(key):
            continue
        result.append(dict(session=group, key=key, name=name, path=path, target=f"{group}:{win}.{pane}"))
    return result


def private_dir():
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)


@contextmanager
def database():
    private_dir()
    with closing(sqlite3.connect(BASE / "events.sqlite3", timeout=5)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, state TEXT, "
                   "name TEXT, summary TEXT, session TEXT, path TEXT, pane TEXT, stamp REAL, seen INTEGER)")
        yield db


def emit(req):
    key, state = req["id"], req["state"]
    if not isinstance(key, str) or not key.strip() or len(key) > 256 or state not in ("waiting", "complete", "clear"):
        raise ValueError("Use a stable run ID and a valid state")
    fields = [req.get(k, "") for k in ("name", "summary", "session", "path", "pane")]
    name, summary, group, path, pane = fields
    if not all(isinstance(v, str) for v in fields) or len(name) > 200 or len(summary) > 4000:
        raise ValueError("Invalid event text")
    if group:
        session(group)
    if path:
        path = str(directory(path))
    if pane and not PANE.fullmatch(pane):
        raise ValueError("Invalid pane identity")
    with database() as db:
        db.execute("""INSERT INTO events VALUES (?,?,?,?,?,?,?,?,0)
            ON CONFLICT(id) DO UPDATE SET state=excluded.state,name=excluded.name,
            summary=excluded.summary,session=excluded.session,path=excluded.path,pane=excluded.pane,
            stamp=excluded.stamp,seen=0
            WHERE events.state != 'complete' AND events.state != excluded.state
            AND (excluded.state = 'complete' OR events.state = 'waiting')""",
                   (key, state, name or key, summary, group, path, pane, time.time()))
    return {}


def inbox():
    with database() as db:
        return [dict(r) for r in db.execute("SELECT * FROM events WHERE state != 'clear' AND seen=0 ORDER BY stamp DESC")]


def current_location():
    if not os.environ.get("TMUX_PANE"):
        return {}
    pane_id = os.environ["TMUX_PANE"]
    if not re.fullmatch(r"%\d+", pane_id):
        return {}
    row = next((p for p in panes() if p["key"].split(":")[1] == pane_id), None)
    return {"session": row["session"], "path": row["path"], "pane": row["key"]} if row else {}


def open_project(req):
    group, root = session(req["session"]), directory(req["path"])
    selected, exact = req.get("choice"), req.get("exact")
    private_dir()
    with (BASE / "open.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        live = [p for p in panes() if p["session"] == group]
        if exact:
            matches = [p for p in live if p["key"] == exact]
        else:
            matches = [p for p in live if Path(p["path"]).resolve().is_relative_to(root)]
            if selected:
                matches = [p for p in matches if p["key"] == selected]
        if len(matches) > 1:
            return {"choices": matches}
        if matches:
            row = matches[0]
        elif selected or exact:
            raise ValueError("That pane closed or changed projects. Open the project again.")
        else:
            if not root.is_dir():
                raise ValueError("Project directory does not exist on this host")
            exists = tmux("has-session", "-t", "="+group, check=False).returncode == 0
            cmd = ["new-window", "-t", "="+group+":"] if exists else ["new-session", "-s", group]
            launch = ["-d", "-P", "-F", "#{pid}:#{pane_id}:#{pane_pid}", "-c", str(root)]
            recipes = Path.home() / ".config/panebell/recipes.json"
            if recipes.exists():
                for recipe in json.loads(recipes.read_text()):
                    if recipe["session"] == group and directory(recipe["path"]) == root:
                        argv = recipe["command"]
                        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
                            raise ValueError("A project recipe needs a non-empty command array")
                        launch.append("exec " + shlex.join(argv))
                        break
            proc = tmux(*cmd, *launch)
            row = next((p for p in panes() if p["session"] == group and p["key"] == proc.stdout.strip()), None)
            if not row:
                raise ValueError("The new shell closed before it could be attached")
        view = view_name(req["client"], group)
        if tmux("has-session", "-t", "="+view, check=False).returncode == 0:
            select(view, row)
        return {"pane": row, "view": view}


def view_name(client, group):
    if not isinstance(client, str) or not re.fullmatch(r"[a-z0-9]{8,32}", client):
        raise ValueError("Invalid local terminal identifier")
    return VIEW + client + "-" + session(group)


def select(view, row):
    win = row["target"].split(":")[1].split(".")[0]
    tmux("select-window", "-t", "="+view+":"+win)
    tmux("select-pane", "-t", row["key"].split(":")[1])


def attach(client, group, pane):
    view = view_name(client, group)
    row = next((p for p in panes() if p["session"] == group and p["key"] == pane), None)
    if not row:
        raise ValueError("The selected pane is no longer available")
    if tmux("has-session", "-t", "="+view, check=False).returncode:
        tmux("new-session", "-d", "-s", view, "-t", "="+group)
    tmux("set-hook", "-t", "="+view, "client-detached",
         "if-shell -F '#{==:#{session_attached},0}' 'kill-session -t =" + view + "'")
    tmux("set-option", "-t", "="+view, "set-titles", "on")
    tmux("set-option", "-t", "="+view, "set-titles-string", view)
    select(view, row)
    os.execvp("tmux", ["tmux", "attach-session", "-t", "="+view])


def dispatch(req):
    action = req["action"]
    if action == "snapshot":
        live = panes()
        keys = {p["key"] for p in live}
        events = [e for e in inbox() if e["state"] != "waiting" or not e["pane"] or e["pane"] in keys]
        return {"events": events, "panes": live}
    if action == "focused":
        view = view_name(req["client"], req["session"])
        result = tmux("display-message", "-p", "-t", "="+view+":",
                      "#{session_name} #{session_attached} #{pid}:#{pane_id}:#{pane_pid}", check=False)
        fields = result.stdout.strip().split()
        if len(fields) == 3 and fields[0] == view and fields[1].isdigit() and int(fields[1]) > 0:
            row = next((p for p in panes() if p["session"] == req["session"] and p["key"] == fields[2]), None)
            return {"pane": row}
        return {"pane": None}
    if action == "open":
        return open_project(req)
    if action == "read":
        with database() as db:
            db.execute("UPDATE events SET seen=1 WHERE id=? AND state='complete'", (req["id"],))
        return {}
    if action == "emit":
        return emit(req)
    raise ValueError("Unknown request")


TERMINAL_SCRIPT = """on run argv
    set theCommand to item 1 of argv
    set identifier to item 2 of argv
    tell application "Terminal"
        activate
        if (count of argv) > 2 then
            repeat with w in windows
                repeat with t in tabs of w
                    if (tty of t is in (items 3 thru -1 of argv)) and busy of t then
                        set selected tab of w to t
                        set index of w to 1
                        return
                    end if
                end repeat
            end repeat
        end if
        set t to do script theCommand
        if identifier is not "" then set custom title of t to identifier
    end tell
end run"""


def terminal(command, title="", ttys=()):
    subprocess.run(["/usr/bin/osascript", "-", command, title, *ttys], input=TERMINAL_SCRIPT,
                   text=True, check=True, timeout=60, stdout=subprocess.DEVNULL)


def attached_ttys(view, client, group):
    result = subprocess.run(["/bin/ps", "-axo", "tty=,command="], text=True, capture_output=True, timeout=4)
    found = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not re.fullmatch(r"ttys[0-9]+", parts[0]):
            continue
        args = parts[1].split()
        is_local = "attach-session" in args and "="+view in args
        is_remote = "host-attach" in args and client in args and group in args
        if is_local or is_remote:
            found.append("/dev/"+parts[0])
    return found


def wezterm(command, view, ttys):
    binary = shutil.which("wezterm") or "/Applications/WezTerm.app/Contents/MacOS/wezterm"
    if not Path(binary).is_file():
        raise ValueError("Install WezTerm or choose terminal in settings")
    cli = [binary, "cli"]
    listed = subprocess.run(cli + ["list", "--format", "json"], text=True, capture_output=True, timeout=5)
    if listed.returncode == 0:
        for pane in json.loads(listed.stdout):
            # tmux sets this title on the dedicated client session below.
            if pane.get("tty_name") in ttys:
                subprocess.run(cli + ["activate-pane", "--pane-id", str(pane["pane_id"])], check=True, timeout=5)
                subprocess.run(["/usr/bin/open", "-a", "WezTerm"], check=True, timeout=5)
                return
        subprocess.run(cli + ["spawn", "--", *command], check=True, stdout=subprocess.DEVNULL, timeout=8)
        subprocess.run(["/usr/bin/open", "-a", "WezTerm"], check=True, timeout=5)
    else:
        subprocess.Popen([binary, "start", "--", *command], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def terminal_action(req):
    prefix = shlex.join([sys.executable, str(Path(__file__).resolve())])
    if req["action"] == "terminal":
        client, group, key = req["client"], req["session"], req["key"]
        title = view_name(client, group)
        if not PANE.fullmatch(key):
            raise ValueError("Invalid pane identity")
        ttys = attached_ttys(title, client, group)
        preferred = config().get("terminal", "terminal")
        if preferred == "wezterm":
            wezterm([sys.executable, str(Path(__file__).resolve()), "attach", client, group, key], title, ttys)
        elif preferred == "terminal":
            terminal(prefix+" attach "+shlex.join([client, group, key]), title, ttys)
        else:
            raise ValueError("Use terminal or wezterm in settings")
    else:
        with tempfile.TemporaryDirectory(prefix="panebell-unlock-") as tmp:
            status = Path(tmp) / "status"
            terminal(prefix+" unlock; echo $? > "+shlex.quote(str(status)))
            for _ in range(480):
                if status.exists() and status.read_text().strip():
                    if status.read_text().strip() != "0":
                        raise ValueError("SSH login did not succeed")
                    break
                time.sleep(0.25)
            else:
                raise ValueError("SSH login timed out")
    return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("request", "serve", "codex-notify", "attach", "host-attach", "unlock"):
        p = sub.add_parser(name)
        if name == "codex-notify":
            p.add_argument("notification")
        if name in ("attach", "host-attach"):
            for key in ("client", "session", "pane"):
                p.add_argument(key)
    p = sub.add_parser("emit", help="Publish a transition on the project host")
    p.add_argument("state", choices=["waiting", "complete", "clear"])
    p.add_argument("id")
    for key in ("name", "summary", "session", "path"):
        p.add_argument("--"+key, default="")
    args = parser.parse_args()
    if args.cmd == "emit":
        event = current_location()
        event.update({k:v for k,v in vars(args).items() if v and k != "cmd"})
        emit(event)
        return
    if args.cmd == "codex-notify":
        event = json.loads(args.notification)
        if event.get("type") != "agent-turn-complete" or not event.get("last-assistant-message"):
            return
        if not event.get("thread-id") or not event.get("turn-id"):
            return
        location = current_location()
        # A closed pane can still deliver its explicit terminal notification.
        if not location and not (os.environ.get("TMUX") and os.environ.get("TMUX_PANE")):
            return
        emit(dict(location, state="complete", id="codex:"+event["thread-id"]+":"+event["turn-id"],
                  name=Path(event.get("cwd") or ".").name or "Codex", summary=event["last-assistant-message"][:4000]))
        return
    if args.cmd == "host-attach":
        return attach(args.client, args.session, args.pane)
    settings = config() if args.cmd in ("attach","request","unlock") else {}
    alias = host(settings)
    ssh = SSH + ["-o", "ControlMaster=auto", "-o", "ControlPersist=120", "-o", "ControlPath=~/.ssh/pb-%C"]
    if args.cmd == "unlock":
        if alias:
            os.execv(ssh[0], ssh+[alias, "true"])
        return
    if args.cmd == "attach":
        if alias:
            view_name(args.client, args.session)
            if not PANE.fullmatch(args.pane):
                raise ValueError("Invalid pane identity")
            os.execv(ssh[0], ssh+["-t", alias, remote_command(settings)+" host-attach "+shlex.join([args.client,args.session,args.pane])])
        return attach(args.client, args.session, args.pane)
    req = json.load(sys.stdin)
    if args.cmd == "request" and req.get("action") in ("finder", "editor"):
        path = req["path"]
        if req["action"] == "finder":
            if alias:
                raise ValueError("Finder needs a local or mounted project directory")
            subprocess.run(["/usr/bin/open", str(directory(path))], check=True)
        else:
            code = shutil.which("code") or "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"
            if alias and not path.startswith("/"):
                raise ValueError("Choose a live pane or use an absolute remote directory for VS Code")
            command = [code, "--remote", "ssh-remote+" + alias, path] if alias else [code, str(directory(path))]
            subprocess.run(command, check=True, timeout=15, stdout=subprocess.DEVNULL)
        print("{}")
        return
    if args.cmd == "request" and req.get("action") in ("terminal", "terminal-unlock"):
        print(json.dumps(terminal_action(req)))
        return
    if alias:
        result = subprocess.run(ssh + ["-o", "BatchMode=yes", alias, remote_command(settings)+" serve"],
                                input=json.dumps(req), text=True, timeout=20, capture_output=True)
        if result.returncode:
            print(result.stderr.strip() or "SSH request failed", file=sys.stderr)
        else:
            print(result.stdout.strip())
        sys.exit(result.returncode)
    print(json.dumps(dispatch(req), ensure_ascii=False))


if __name__ == "__main__":
    os.umask(0o077)
    os.environ["PATH"] = os.environ.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin"
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError) as error:
        sys.exit(str(error))
