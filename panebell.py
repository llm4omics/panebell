#!/usr/bin/env python3
"""Local tmux controller and event inbox. The same file runs over SSH."""
import argparse
from contextlib import closing, contextmanager
import fcntl
import hashlib
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
import uuid
import tempfile

BASE = Path(os.environ.get("PANEBELL_STATE_DIR") or Path.home() / ".local/state/panebell")
CONFIG = Path(os.environ.get("PANEBELL_CONFIG") or Path.home() / ".config/panebell/config.json")
REMOTE = 'python3 "$HOME/.local/lib/panebell/panebell.py"'
SSH = ["/usr/bin/ssh", "-o", "ConnectTimeout=6", "-o", "ServerAliveInterval=8", "-o", "ServerAliveCountMax=2"]
SESSION = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,79}")
PANE = re.compile(r"\d+:%\d+:\d+")
VIEW = "pb-view-"
CLAUDE_EVENTS = ("PermissionRequest", "PostToolUse", "PostToolUseFailure", "Stop", "SessionEnd")
DEFAULT_CLAUDE_HOOK = 'python3 "$HOME/.local/lib/panebell/panebell.py" claude-hook'


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


def claude_hook(event):
    """Observe permission lifecycle without sending decisions or retaining tool input."""
    kind, sid = event.get("hook_event_name"), event.get("session_id")
    if not isinstance(sid, str) or not sid or kind not in (
            "PermissionRequest", "PostToolUse", "PostToolUseFailure", "Stop", "SessionEnd"):
        return
    if kind != "PermissionRequest" and not (BASE / "events.sqlite3").exists():
        return
    location = current_location() if kind == "PermissionRequest" else {}
    if kind == "PermissionRequest" and not location:
        return
    signature = hashlib.sha256(json.dumps([event.get("tool_name"), event.get("tool_input")],
                                          sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS permission_waits (sid TEXT, signature TEXT, event TEXT, "
                   "PRIMARY KEY (sid,signature))")
        if kind == "PermissionRequest":
            receipt = "permission:" + uuid.uuid4().hex
            added = db.execute("INSERT OR IGNORE INTO permission_waits VALUES (?,?,?)", (sid, signature, receipt))
            if added.rowcount:
                tool = str(event.get("tool_name") or "tool")[:120]
                db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,0)",
                           (receipt, "waiting", Path(location["path"]).name or "Claude Code",
                            "Claude Code requests permission to use " + tool + ".", location["session"],
                            location["path"], location["pane"], time.time()))
        else:
            where, values = ("sid=?", (sid,)) if kind in ("Stop", "SessionEnd") else ("sid=? AND signature=?", (sid,signature))
            db.execute("UPDATE events SET state='clear' WHERE id IN (SELECT event FROM permission_waits WHERE "+where+")",
                       values)
            db.execute("DELETE FROM permission_waits WHERE "+where, values)


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


def safe_panes():
    if not shutil.which("tmux"):
        return []
    try:
        return panes()
    except ValueError:
        return []


def atomic_write_text(path, content, backup_name=""):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    mode = (path.stat().st_mode & 0o777) if path.is_file() else 0o600
    if backup_name and path.is_file():
        bdir = BASE / "backups"
        bdir.mkdir(mode=0o700, parents=True, exist_ok=True)
        bpath = bdir / backup_name
        if not bpath.exists():
            bpath.write_bytes(path.read_bytes())
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix="." + path.name + ".", delete=False) as out:
        out.write(content)
        tmp = Path(out.name)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def discover_projects(live, existing, home=None, prefer_tilde=True):
    if not isinstance(existing, list) or not all(isinstance(p, dict) for p in existing):
        raise ValueError("Configuration 'projects' must be a JSON array of objects")
    merged = list(existing)
    seen = set()
    names = {p.get("name", "") for p in merged if isinstance(p.get("name"), str)}
    home_resolved = (Path(home).expanduser().resolve() if home else Path.home().resolve()) if prefer_tilde else None
    remote_home = str(home or "").rstrip("/") if not prefer_tilde else ""

    for item in merged:
        sess, raw = item.get("session", ""), item.get("path", "")
        if not isinstance(sess, str) or not isinstance(raw, str) or not sess or not raw:
            continue
        if prefer_tilde:
            try:
                norm = str(Path(raw).expanduser().resolve())
            except OSError:
                norm = raw.rstrip("/") or "/"
        else:
            norm = raw.rstrip("/") or "/"
        seen.add((sess, norm))

    added = []
    for row in live:
        sess, raw = row.get("session", ""), row.get("path", "")
        if not sess or not raw or sess.startswith(VIEW) or not SESSION.fullmatch(sess):
            continue
        if prefer_tilde:
            resolved = Path(raw).expanduser().resolve()
            if resolved == Path("/") or resolved == home_resolved:
                continue
            norm = str(resolved)
            out_path = "~/" + str(resolved.relative_to(home_resolved)) if resolved.is_relative_to(home_resolved) else norm
        else:
            norm = raw.rstrip("/") or "/"
            if norm == "/" or (remote_home and norm == remote_home):
                continue
            out_path = norm
        if (sess, norm) in seen:
            continue
        seen.add((sess, norm))
        leaf = Path(out_path.rstrip("/")).name or sess
        if leaf not in names:
            chosen = leaf
        elif f"{leaf} ({sess})" not in names:
            chosen = f"{leaf} ({sess})"
        else:
            parent = Path(out_path.rstrip("/")).parent.name or "root"
            chosen = f"{leaf} ({sess}:{parent})"
        names.add(chosen)
        entry = {"name": chosen, "session": sess, "path": out_path}
        merged.append(entry)
        added.append(entry)
    return merged, added


def merge_claude_hooks(settings_path, helper_cmd=DEFAULT_CLAUDE_HOOK, dry_run=False):
    if settings_path.exists():
        raw = settings_path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {settings_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Expected a JSON object in {settings_path}")
    else:
        data = {}

    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
    elif not isinstance(hooks, dict):
        raise ValueError(f"Expected 'hooks' in {settings_path} to be a JSON object")

    added = []
    for event_name in CLAUDE_EVENTS:
        groups = hooks.get(event_name)
        if groups is None:
            groups = []
        elif not isinstance(groups, list):
            raise ValueError(f"Expected 'hooks.{event_name}' in {settings_path} to be a list")
        already = any(
            isinstance(g, dict)
            and isinstance(g.get("hooks"), list)
            and any(
                isinstance(h, dict)
                and isinstance(h.get("command"), str)
                and "panebell.py" in h["command"]
                and "claude-hook" in h["command"]
                for h in g["hooks"]
            )
            for g in groups
        )
        if not already:
            groups = list(groups) + [{"hooks": [{"type": "command", "command": helper_cmd}]}]
            hooks[event_name] = groups
            added.append(event_name)

    if added and not dry_run:
        data["hooks"] = hooks
        atomic_write_text(settings_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                          backup_name="claude-settings.json.bak")
    return {"path": str(settings_path), "changed": bool(added), "added_events": added}


def _skip_toml_quoted(raw, i):
    quote = raw[i]
    triple = raw[i:i + 3] == quote * 3
    delim = quote * 3 if triple else quote
    i += len(delim)
    n = len(raw)
    while i < n:
        if quote == '"' and raw[i] == "\\":
            i += 2
            continue
        if raw.startswith(delim, i):
            i += len(delim)
            while triple and i < n and raw[i] == quote:
                i += 1
            return i
        if not triple and raw[i] == "\n":
            raise ValueError("Unterminated TOML string")
        i += 1
    raise ValueError("Unterminated TOML string")


def _scan_toml_top_level(raw):
    n = len(raw)
    i = 0
    entries = []
    while i < n:
        line_start = i
        while i < n and raw[i] in " \t\r":
            i += 1
        if i >= n:
            return n, entries
        if raw[i] in "\n#":
            while i < n and raw[i] != "\n":
                i += 1
            i += 1
            continue
        if raw[i] == "[":
            return line_start, entries

        parts = []
        while True:
            while i < n and raw[i] in " \t":
                i += 1
            if i < n and raw[i] in ('"', "'"):
                end_q = _skip_toml_quoted(raw, i)
                if end_q - i >= 6 and raw[i:i + 3] in ('"""', "'''"):
                    raise ValueError("Multiline key is invalid in TOML")
                token = json.loads(raw[i:end_q]) if raw[i] == '"' else raw[i + 1:end_q - 1]
                parts.append(token)
                i = end_q
            else:
                m = re.match(r"[A-Za-z0-9_-]+", raw[i:])
                if not m:
                    raise ValueError("Invalid TOML top-level key")
                parts.append(m.group(0))
                i += m.end()
            while i < n and raw[i] in " \t":
                i += 1
            if i < n and raw[i] == ".":
                i += 1
                continue
            break

        if i >= n or raw[i] != "=":
            raise ValueError("Expected '=' after TOML key")
        i += 1
        while i < n and raw[i] in " \t":
            i += 1
        val_start = i
        depth = 0
        val_end = i
        while i < n:
            ch = raw[i]
            if ch in ('"', "'"):
                i = _skip_toml_quoted(raw, i)
                val_end = i
                continue
            if ch in "[{":
                depth += 1
                i += 1
                val_end = i
                continue
            if ch in "]}":
                depth = max(0, depth - 1)
                i += 1
                val_end = i
                continue
            if ch == "#":
                while i < n and raw[i] != "\n":
                    i += 1
                if depth == 0:
                    break
                continue
            if ch == "\n":
                if depth == 0:
                    break
                i += 1
                continue
            if ch not in " \t\r":
                val_end = i + 1
            i += 1
        if depth != 0:
            raise ValueError("Unclosed bracket in TOML value")
        entries.append((tuple(parts), val_start, val_end))
        if i < n and raw[i] == "\n":
            i += 1
    return n, entries


def _parse_toml_str_array(expr):
    s = expr.strip()
    if not (s.startswith("[") and s.endswith("]")):
        raise ValueError("Expected TOML array for 'notify'")
    inner = s[1:-1]
    items = []
    i, n = 0, len(inner)
    expect_item = True
    while i < n:
        while i < n and inner[i] in " \t\r\n":
            i += 1
        if i < n and inner[i] == "#":
            while i < n and inner[i] != "\n":
                i += 1
            continue
        if i >= n:
            break
        if inner[i] == ",":
            if expect_item:
                raise ValueError("Unexpected comma in TOML array")
            expect_item = True
            i += 1
            continue
        if not expect_item or inner[i] not in ('"', "'"):
            raise ValueError("Expected string element in 'notify' array")
        end_q = _skip_toml_quoted(inner, i)
        quote = inner[i]
        triple = end_q - i >= 6 and inner[i:i + 3] == quote * 3
        if quote == '"' and not triple:
            items.append(json.loads(inner[i:end_q]))
        elif quote == "'" and not triple:
            items.append(inner[i + 1:end_q - 1])
        else:
            body = inner[i + 3:end_q - 3]
            if body.startswith("\n"):
                body = body[1:]
            elif body.startswith("\r\n"):
                body = body[2:]
            items.append(body if quote == "'" else json.loads('"' + body.replace('"', '\\"').replace("\n", "\\n") + '"'))
        i = end_q
        expect_item = False
    return items


def _verify_toml_if_supported(content):
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        return
    tomllib.loads(content)


def merge_codex_notify(config_path, helper_path, python_cmd="python3", dry_run=False):
    helper_str = str(Path(helper_path).expanduser())
    target_argv = [python_cmd, helper_str, "codex-notify"]
    raw = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    _verify_toml_if_supported(raw)
    first_table, entries = _scan_toml_top_level(raw)
    notify_entries = [e for e in entries if e[0] == ("notify",)]
    if len(notify_entries) > 1:
        raise ValueError(f"Duplicate top-level 'notify' keys in {config_path}")

    mode = "unchanged"
    if notify_entries:
        _, vstart, vend = notify_entries[0]
        existing = _parse_toml_str_array(raw[vstart:vend])
        if len(existing) >= 3 and existing[2] == "codex-notify" and Path(existing[1]).name == "panebell.py":
            desired = [existing[0], helper_str, *existing[2:]]
            if existing == desired:
                return {"path": str(config_path), "changed": False, "mode": "unchanged", "notify": existing}
            mode = "updated"
        elif existing:
            forward_payload = json.dumps(existing, separators=(",", ":"), ensure_ascii=False)
            desired = [python_cmd, helper_str, "codex-notify", "--forward", forward_payload]
            mode = "chained"
        else:
            desired = target_argv
            mode = "set"
        rendered = json.dumps(desired, ensure_ascii=False)
        new_text = raw[:vstart] + rendered + raw[vend:]
    else:
        desired = target_argv
        mode = "set"
        line = "notify = " + json.dumps(desired, ensure_ascii=False) + "\n"
        if not raw:
            new_text = line
        elif first_table == 0:
            new_text = line + "\n" + raw
        else:
            head = raw[:first_table]
            tail = raw[first_table:]
            if not head.endswith("\n"):
                head += "\n"
            sep = "" if not tail or head.endswith("\n\n") else "\n"
            stripped = head.rstrip(" \t\r\n")
            new_text = (stripped + "\n" + line + sep + tail) if stripped else (line + ("\n" if tail else "") + tail)

    _verify_toml_if_supported(new_text)
    if not dry_run:
        atomic_write_text(config_path, new_text, backup_name="codex-config.toml.bak")
    return {"path": str(config_path), "changed": True, "mode": mode, "notify": desired}


def _agent_detected(flag, config_file, binary_name, extra_bins=()):
    if flag is not None:
        return flag
    return (
        config_file.exists()
        or config_file.parent.is_dir()
        or bool(shutil.which(binary_name))
        or any(Path(p).is_file() for p in extra_bins)
    )


def run_setup(args):
    cfg_path = Path(args.config).expanduser() if args.config else CONFIG
    helper_path = Path(args.helper_path).expanduser() if args.helper_path else (Path.home() / ".local/lib/panebell/panebell.py")
    existing_cfg = {"sshHost": "", "projects": []}
    if cfg_path.exists():
        raw_cfg = cfg_path.read_text(encoding="utf-8")
        if raw_cfg.strip():
            loaded = json.loads(raw_cfg)
            if not isinstance(loaded, dict):
                raise ValueError("Configuration must be a JSON object")
            existing_cfg = dict(loaded)
    existing_cfg.setdefault("sshHost", "")
    existing_cfg.setdefault("projects", [])

    alias = host({"sshHost": args.ssh_host}) if args.ssh_host is not None else host(existing_cfg)
    report = {"mode": "host-only" if args.host_only else ("ssh" if alias else "local"), "dry_run": args.dry_run}

    if alias and not args.host_only:
        ssh_cmd = SSH + ["-o", "BatchMode=yes", "-o", "ControlMaster=auto", "-o", "ControlPersist=120", "-o", "ControlPath=~/.ssh/pb-%C"]
        custom_helper = existing_cfg.get("remoteHelper")
        if custom_helper is not None:
            remote_command(existing_cfg)
        remote_dest = shlex.quote(custom_helper) if custom_helper else '"$HOME/.local/lib/panebell/panebell.py"'
        remote_flags = ["setup", "--host-only", "--json"]
        if args.dry_run:
            remote_flags.append("--dry-run")
        if args.claude is True:
            remote_flags.append("--claude")
        elif args.claude is False:
            remote_flags.append("--no-claude")
        if args.codex is True:
            remote_flags.append("--codex")
        elif args.codex is False:
            remote_flags.append("--no-codex")
        if args.dry_run:
            probe = subprocess.run(
                ssh_cmd + [alias, "python3 - " + " ".join(shlex.quote(f) for f in remote_flags)],
                input=Path(__file__).resolve().read_text(encoding="utf-8"),
                text=True, timeout=25, capture_output=True,
            )
        else:
            install_and_run = (
                f'target={remote_dest}; dir=$(dirname "$target"); mkdir -p "$dir"; '
                'tmp=$(mktemp "$dir/.install.XXXXXX"); cat > "$tmp"; chmod 755 "$tmp"; mv "$tmp" "$target"; '
                'python3 "$target" ' + " ".join(shlex.quote(f) for f in remote_flags)
            )
            probe = subprocess.run(
                ssh_cmd + [alias, install_and_run],
                input=Path(__file__).resolve().read_text(encoding="utf-8"),
                text=True, timeout=25, capture_output=True,
            )
        if probe.returncode:
            raise ValueError(probe.stderr.strip() or f"SSH setup on '{alias}' failed (verify 'ssh {alias} true')")
        remote_rep = json.loads(probe.stdout)
        merged_projects, added_projects = discover_projects(
            remote_rep.get("live_panes", []),
            existing_cfg.get("projects", []),
            home=remote_rep.get("home", ""),
            prefer_tilde=False,
        )
        cfg_changed = (not cfg_path.exists()) or (existing_cfg.get("sshHost", "") != alias) or bool(added_projects)
        existing_cfg["sshHost"] = alias
        existing_cfg["projects"] = merged_projects
        if cfg_changed and not args.dry_run:
            backup = "config.json.bak" if cfg_path.exists() and (existing_cfg["projects"] != added_projects) else ""
            atomic_write_text(cfg_path, json.dumps(existing_cfg, indent=2, ensure_ascii=False) + "\n", backup_name=backup)
        report.update({
            "ssh_host": alias,
            "config": {"path": str(cfg_path), "changed": cfg_changed,
                       "total_projects": len(merged_projects), "added_projects": added_projects},
            "claude": remote_rep.get("claude"),
            "codex": remote_rep.get("codex"),
        })
        return report

    if not args.dry_run and Path(__file__).resolve() != helper_path.resolve():
        helper_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", dir=helper_path.parent, prefix=".install.", delete=False) as out:
            out.write(Path(__file__).resolve().read_bytes())
            tmp_h = Path(out.name)
        os.chmod(tmp_h, 0o755)
        os.replace(tmp_h, helper_path)

    live = safe_panes()
    if args.host_only:
        report["home"] = str(Path.home())
        report["live_panes"] = live
    else:
        merged_projects, added_projects = discover_projects(live, existing_cfg.get("projects", []), prefer_tilde=True)
        cfg_changed = (not cfg_path.exists()) or (existing_cfg.get("sshHost", "") != "") or bool(added_projects)
        existing_cfg["sshHost"] = ""
        existing_cfg["projects"] = merged_projects
        if cfg_changed and not args.dry_run:
            backup = "config.json.bak" if cfg_path.exists() and (len(merged_projects) > len(added_projects)) else ""
            atomic_write_text(cfg_path, json.dumps(existing_cfg, indent=2, ensure_ascii=False) + "\n", backup_name=backup)
        report["config"] = {
            "path": str(cfg_path),
            "changed": cfg_changed,
            "total_projects": len(merged_projects),
            "added_projects": added_projects,
        }

    home = Path.home()
    claude_path = Path(args.claude_settings).expanduser() if args.claude_settings else (home / ".claude/settings.json")
    if args.claude_settings or _agent_detected(
        args.claude, claude_path, "claude", (home / ".local/bin/claude", home / ".claude/local/claude")
    ):
        custom_hook = DEFAULT_CLAUDE_HOOK if helper_path == (home / ".local/lib/panebell/panebell.py") else f"python3 {shlex.quote(str(helper_path))} claude-hook"
        report["claude"] = merge_claude_hooks(claude_path, helper_cmd=custom_hook, dry_run=args.dry_run)
    else:
        report["claude"] = {"path": str(claude_path), "changed": False, "skipped": "not_detected"}

    codex_home = Path(os.environ.get("CODEX_HOME") or (home / ".codex")).expanduser()
    codex_path = Path(args.codex_config).expanduser() if args.codex_config else (codex_home / "config.toml")
    if args.codex_config or _agent_detected(args.codex, codex_path, "codex", (home / ".local/bin/codex",)):
        report["codex"] = merge_codex_notify(codex_path, helper_path=helper_path, dry_run=args.dry_run)
    else:
        report["codex"] = {"path": str(codex_path), "changed": False, "skipped": "not_detected"}
    return report


def format_setup_summary(rep):
    prefix = "[dry-run] " if rep.get("dry_run") else ""
    lines = []
    cfg = rep.get("config")
    if cfg:
        added = len(cfg["added_projects"])
        lines.append(f"{prefix}Settings: {cfg['path']} ({cfg['total_projects']} project(s), {added} added)")
        for item in cfg["added_projects"]:
            lines.append(f"  + {item['name']} ({item['session']} -> {item['path']})")
    claude = rep.get("claude") or {}
    if claude.get("skipped"):
        lines.append(f"{prefix}Claude Code: skipped ({claude['path']} not detected; pass --claude to create)")
    elif claude.get("changed"):
        lines.append(f"{prefix}Claude Code: merged {', '.join(claude['added_events'])} in {claude['path']}")
    else:
        lines.append(f"{prefix}Claude Code: already configured in {claude['path']}")
    codex = rep.get("codex") or {}
    if codex.get("skipped"):
        lines.append(f"{prefix}Codex CLI: skipped ({codex['path']} not detected; pass --codex to create)")
    elif codex.get("changed"):
        lines.append(f"{prefix}Codex CLI: {codex['mode']} notify in {codex['path']}")
    else:
        lines.append(f"{prefix}Codex CLI: already configured in {codex['path']}")
    return "\n".join(lines)


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
    for name in ("request", "serve", "codex-notify", "claude-hook", "attach", "host-attach", "unlock"):
        p = sub.add_parser(name)
        if name == "codex-notify":
            p.add_argument("--forward", default="", help="JSON argv of an existing notify command to chain")
            p.add_argument("notification")
        if name in ("attach", "host-attach"):
            for key in ("client", "session", "pane"):
                p.add_argument(key)
    p = sub.add_parser("emit", help="Publish a transition on the project host")
    p.add_argument("state", choices=["waiting", "complete", "clear"])
    p.add_argument("id")
    for key in ("name", "summary", "session", "path"):
        p.add_argument("--"+key, default="")
    sp = sub.add_parser("setup", help="Scan active tmux sessions and merge Claude Code / Codex settings")
    sp.add_argument("--config", default="")
    sp.add_argument("--helper-path", default="")
    sp.add_argument("--ssh-host", default=None)
    sp.add_argument("--host-only", action="store_true")
    sp.add_argument("--claude", dest="claude", action="store_true", default=None)
    sp.add_argument("--no-claude", dest="claude", action="store_false")
    sp.add_argument("--codex", dest="codex", action="store_true", default=None)
    sp.add_argument("--no-codex", dest="codex", action="store_false")
    sp.add_argument("--claude-settings", default="")
    sp.add_argument("--codex-config", default="")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.cmd == "setup":
        rep = run_setup(args)
        print(json.dumps(rep, ensure_ascii=False) if args.json else format_setup_summary(rep))
        return
    if args.cmd == "emit":
        event = current_location()
        event.update({k:v for k,v in vars(args).items() if v and k != "cmd"})
        emit(event)
        return
    if args.cmd == "claude-hook":
        claude_hook(json.load(sys.stdin))
        return
    if args.cmd == "codex-notify":
        forward_argv = None
        if args.forward:
            forward_argv = json.loads(args.forward)
            if not isinstance(forward_argv, list) or not forward_argv or not all(isinstance(x, str) and x for x in forward_argv):
                raise ValueError("Invalid --forward command list")
        try:
            event = json.loads(args.notification)
        except json.JSONDecodeError:
            event = {}
        if (
            isinstance(event, dict)
            and event.get("type") == "agent-turn-complete"
            and event.get("last-assistant-message")
            and event.get("thread-id")
            and event.get("turn-id")
        ):
            location = current_location()
            # A closed pane can still deliver its explicit terminal notification.
            if location or (os.environ.get("TMUX") and os.environ.get("TMUX_PANE")):
                emit(dict(location, state="complete", id="codex:"+event["thread-id"]+":"+event["turn-id"],
                          name=Path(event.get("cwd") or ".").name or "Codex", summary=event["last-assistant-message"][:4000]))
        if forward_argv:
            res = subprocess.run([*forward_argv, args.notification], check=False)
            if res.returncode:
                sys.exit(res.returncode)
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
