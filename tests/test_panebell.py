import concurrent.futures
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import panebell as pb


class InboxTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        env = patch.object(pb, "BASE", self.base)
        env.start()
        self.addCleanup(env.stop)

    def test_wait_finish_replay_ack(self):
        event = dict(id="demo-r1", name="Paper Planes", summary="Colours")
        pb.emit(dict(event, state="waiting"))
        pb.dispatch(dict(action="read", id=event["id"]))
        self.assertEqual(pb.inbox()[0]["state"], "waiting")
        pb.emit(dict(event, state="clear"))
        self.assertEqual(pb.inbox(), [])
        pb.emit(dict(event, state="waiting"))
        self.assertEqual(pb.inbox(), [])
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda _: pb.emit(dict(event, state="complete")), range(8)))
        self.assertEqual([e["id"] for e in pb.inbox()], ["demo-r1"])
        pb.dispatch(dict(action="read", id=event["id"]))
        for state in ("waiting", "clear", "complete"):
            pb.emit(dict(event, state=state))
        self.assertEqual(pb.inbox(), [])

    def test_opt_in_codex_callback(self):
        env = dict(os.environ, PANEBELL_STATE_DIR=str(self.base))
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        msg = {"type":"agent-turn-complete","thread-id":"sample-thread","turn-id":"sample-turn",
               "cwd":"/","last-assistant-message":"Done"}
        cmd = [sys.executable, pb.__file__, "codex-notify", json.dumps(msg)]
        subprocess.run(cmd, env=env, check=True)
        self.assertEqual(pb.inbox(), [])
        # A completed notification can arrive after the terminal has closed.
        env.update(TMUX="/unused,1,0", TMUX_PANE="%12", TMUX_TMPDIR=str(self.base))
        subprocess.run(cmd, env=env, check=True)
        subprocess.run(cmd, env=env, check=True)
        self.assertEqual([row["id"] for row in pb.inbox()], ["codex:sample-thread:sample-turn"])

    def test_host_validation(self):
        self.assertEqual(pb.host({"sshHost":"sample-host"}), "sample-host")
        with self.assertRaises(ValueError):
            pb.host({"sshHost":"-Fother"})
        with self.assertRaises(ValueError):
            pb.open_project(dict(session="invalid:session", path="~"))


@unittest.skipUnless(shutil.which("tmux"), "requires tmux")
class WindowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.sock = str(self.base / "mux")
        self.fake = self.base / "bin"
        self.fake.mkdir()
        binary = shutil.which("tmux")
        wrapper = self.fake / "tmux"
        wrapper.write_text("#!/bin/sh\nexec "+shlex.quote(binary)+" -f /dev/null -S "+shlex.quote(self.sock)+' "$@"\n')
        wrapper.chmod(0o755)
        env = patch.dict(os.environ, {"PATH":str(self.fake)+":"+os.environ["PATH"],"TMUX":""})
        env.start()
        self.addCleanup(env.stop)
        state = patch.object(pb, "BASE", self.base / "state")
        state.start()
        self.addCleanup(state.stop)
        pb.tmux("new-session","-d","-s","anchor","sleep 180")
        self.addCleanup(pb.tmux, "kill-server", check=False)
        pb.tmux("set-option","-g","default-shell","/bin/sh")
        pb.tmux("set-option","-g","default-command","/bin/sh -i")
        self.a = self.base / "paper planes"
        self.b = self.base / "paper planes-extra"
        self.a.mkdir()
        self.b.mkdir()
        self.req = dict(action="open",client="synthetic123",session="sketch",path=str(self.a))

    def open(self, **args):
        return pb.dispatch(dict(self.req, **args))

    def test_route_by_identity_or_directory(self):
        first = self.open()["pane"]
        self.assertEqual(self.open()["pane"]["key"], first["key"])
        pb.tmux("rename-window", "-t", first["target"].split(".")[0], "renamed")
        self.assertEqual(self.open()["pane"]["key"], first["key"])
        pb.tmux("send-keys","-t",first["target"], "cd "+shlex.quote(str(self.b)), "Enter")
        for _ in range(100):
            if next(p for p in pb.panes() if p["key"]==first["key"])["path"]==str(self.b): break
            time.sleep(.02)
        new = self.open()["pane"]
        self.assertNotEqual(new["key"], first["key"])
        self.assertEqual(self.open(exact=first["key"])["pane"]["path"], str(self.b))
        sub = self.a / "origami"
        sub.mkdir()
        pb.tmux("new-window","-d","-t","=sketch:","-c",str(sub))
        for _ in range(100):
            if {p["path"] for p in pb.panes() if p["session"] == "sketch"} >= {str(self.a), str(sub)}: break
            time.sleep(.02)
        choices = self.open()["choices"]
        self.assertEqual({r["path"] for r in choices}, {str(sub), str(self.a)})
        chosen = self.open(choice=new["key"])["pane"]
        pb.tmux("kill-window","-t",chosen["target"].split(".")[0])
        with self.assertRaises(ValueError): self.open(choice=chosen["key"])
        self.assertEqual(self.open()["pane"]["path"],str(sub))

    def test_closed_wait_is_hidden_without_manufacturing_completion(self):
        pane = self.open()["pane"]
        pb.emit(dict(id="fold", state="waiting", session="sketch", path=str(self.a), pane=pane["key"]))
        self.assertEqual(len(pb.dispatch({"action":"snapshot"})["events"]), 1)
        pb.tmux("kill-session", "-t", "=sketch")
        self.assertEqual(pb.dispatch({"action":"snapshot"})["events"], [])
        self.assertEqual(pb.inbox()[0]["state"], "waiting")

    def test_permission_hook_observes_only_and_clears_on_resolution(self):
        pane = self.open()["pane"]
        env = dict(os.environ, PANEBELL_STATE_DIR=str(pb.BASE), TMUX_PANE=pane["key"].split(":")[1])
        cmd = [sys.executable, pb.__file__, "claude-hook"]
        def send(kind, tool="Read"):
            payload = dict(session_id="fictional-session", hook_event_name=kind, tool_name=tool,
                           tool_input={"file_path":"fixture-only-input"})
            result = subprocess.run(cmd, env=env, input=json.dumps(payload), text=True,
                                    capture_output=True, check=True)
            self.assertEqual(result.stdout, "")
        send("PermissionRequest")
        send("PermissionRequest")
        send("PermissionRequest", "Write")
        self.assertEqual(len(pb.inbox()), 2)
        self.assertTrue(all("fixture-only-input" not in e["summary"] for e in pb.inbox()))
        send("PostToolUse")
        self.assertEqual(len(pb.inbox()), 1)
        send("PostToolUseFailure", "Write")
        self.assertEqual(pb.inbox(), [])
        send("PermissionRequest")
        send("SessionEnd")
        self.assertEqual(pb.inbox(), [])

    def test_disconnected_inbox_through_cli(self):
        pane = self.open()["pane"]
        cfg = self.base / "config.json"
        cfg.write_text('{"projects":[]}')
        env = dict(os.environ, PANEBELL_CONFIG=str(cfg), PANEBELL_STATE_DIR=str(pb.BASE),
                   TMUX_PANE=pane["key"].split(":")[1])
        cmd = [sys.executable, pb.__file__]
        for state in ("waiting","complete","complete"):
            subprocess.run(cmd+["emit",state,"rainbow","--name","Lantern Atlas"],env=env,check=True)
        pb.tmux("kill-session","-t","=sketch")
        def rpc(action, **fields):
            res = subprocess.run(cmd+["request"], env=env, input=json.dumps(dict(action=action,**fields)),
                                 text=True,capture_output=True,check=True)
            return json.loads(res.stdout)
        self.assertEqual([x["state"] for x in rpc("snapshot")["events"]], ["complete"])
        rpc("read", id="rainbow")
        self.assertEqual(rpc("snapshot")["events"],[])

    def test_setup_scans_tmux_and_losslessly_merges_claude_and_codex(self):
        fake_home = self.base / "home"
        proj_a = fake_home / "Sandbox/paper-planes"
        proj_b = fake_home / "Sandbox/lantern-atlas"
        proj_a.mkdir(parents=True)
        proj_b.mkdir(parents=True)
        pb.tmux("new-session", "-d", "-s", "sketchbook", "-c", str(proj_a))
        pb.tmux("kill-session", "-t", "=anchor")
        pb.tmux("new-window", "-d", "-t", "=sketchbook:", "-c", str(proj_a))
        pb.tmux("new-window", "-d", "-t", "=sketchbook:", "-c", str(proj_b))
        pb.tmux("new-session", "-d", "-s", "home_only", "-c", str(fake_home))

        cfg = fake_home / ".config/panebell/config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({
            "sshHost": "",
            "terminal": "wezterm",
            "projects": [{"name": "Paper Planes Custom", "session": "sketchbook", "path": "~/Sandbox/paper-planes"}],
        }))

        claude_cfg = fake_home / ".claude/settings.json"
        claude_cfg.parent.mkdir(parents=True)
        claude_cfg.write_text(json.dumps({
            "model": "sonnet",
            "hooks": {
                "PermissionRequest": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo existing-audit"}]}
                ]
            },
        }))

        forward_log = self.base / "forwarded.json"
        forward_script = self.base / "existing_notify.py"
        forward_script.write_text(
            "#!/usr/bin/env python3\nimport pathlib, sys\n"
            f"pathlib.Path({str(forward_log)!r}).write_text(sys.argv[-1])\n"
        )
        forward_script.chmod(0o755)

        codex_cfg = fake_home / ".codex/config.toml"
        codex_cfg.parent.mkdir(parents=True)
        codex_cfg.write_text(
            '# User Codex settings\n'
            'model = "o3"\n'
            f'notify = [{json.dumps(sys.executable)}, {json.dumps(str(forward_script))}] # keep bell\n\n'
            '[projects."~/Sandbox/paper-planes"]\n'
            'trust_level = "trusted"\n'
        )

        helper_dest = fake_home / ".local/lib/panebell/panebell.py"
        env = dict(os.environ, HOME=str(fake_home), PANEBELL_CONFIG=str(cfg), PANEBELL_STATE_DIR=str(pb.BASE))
        cmd = [sys.executable, pb.__file__, "setup", "--helper-path", str(helper_dest), "--json"]

        # 1. Dry-run leaves every file untouched
        dry = json.loads(subprocess.run(cmd + ["--dry-run"], env=env, text=True, capture_output=True, check=True).stdout)
        self.assertTrue(dry["config"]["changed"])
        self.assertEqual([p["path"] for p in dry["config"]["added_projects"]], ["~/Sandbox/lantern-atlas"])
        self.assertFalse(helper_dest.exists())
        self.assertEqual(len(json.loads(cfg.read_text())["projects"]), 1)

        # 2. Apply setup and verify lossless merges + bare-home filtering + deduplication
        applied = json.loads(subprocess.run(cmd, env=env, text=True, capture_output=True, check=True).stdout)
        self.assertTrue(helper_dest.is_file())
        saved_cfg = json.loads(cfg.read_text())
        self.assertEqual(saved_cfg["terminal"], "wezterm")
        self.assertEqual(saved_cfg["projects"], [
            {"name": "Paper Planes Custom", "session": "sketchbook", "path": "~/Sandbox/paper-planes"},
            {"name": "lantern-atlas", "session": "sketchbook", "path": "~/Sandbox/lantern-atlas"},
        ])

        saved_claude = json.loads(claude_cfg.read_text())
        self.assertEqual(saved_claude["model"], "sonnet")
        self.assertEqual(len(saved_claude["hooks"]["PermissionRequest"]), 2)
        self.assertEqual(saved_claude["hooks"]["PermissionRequest"][0]["matcher"], "Bash")
        for ev in pb.CLAUDE_EVENTS:
            self.assertTrue(any("claude-hook" in h["command"] for g in saved_claude["hooks"][ev] for h in g["hooks"]))

        saved_codex_raw = codex_cfg.read_text()
        self.assertIn("# keep bell", saved_codex_raw)
        self.assertIn('[projects."~/Sandbox/paper-planes"]', saved_codex_raw)
        self.assertLess(saved_codex_raw.index("notify ="), saved_codex_raw.index('[projects."~/Sandbox/paper-planes"]'))
        self.assertEqual(applied["codex"]["mode"], "chained")

        # 3. Re-running setup is strictly idempotent
        rerun = json.loads(subprocess.run(cmd, env=env, text=True, capture_output=True, check=True).stdout)
        self.assertFalse(rerun["config"]["changed"])
        self.assertFalse(rerun["claude"]["changed"])
        self.assertFalse(rerun["codex"]["changed"])

        # 4. Chained Codex notify records inbox receipt AND forwards to user's original notifier
        pane = next(p for p in pb.panes() if p["session"] == "sketchbook")
        notify_argv = applied["codex"]["notify"]
        payload = json.dumps({
            "type": "agent-turn-complete",
            "thread-id": "t-chain",
            "turn-id": "u-1",
            "cwd": str(proj_a),
            "last-assistant-message": "Completed origami check.",
        })
        notify_env = dict(env, TMUX=self.sock + ",1,0", TMUX_PANE=pane["key"].split(":")[1])
        subprocess.run([*notify_argv, payload], env=notify_env, check=True)
        self.assertEqual(json.loads(forward_log.read_text())["turn-id"], "u-1")
        self.assertTrue(any(e["id"] == "codex:t-chain:u-1" for e in pb.inbox()))

    def test_codex_toml_insertion_before_leading_table(self):
        codex_cfg = self.base / "leading_table.toml"
        codex_cfg.write_text('[mcp_servers.sample]\ncommand = "python3"\n')
        res = pb.merge_codex_notify(codex_cfg, self.base / "panebell.py")
        self.assertEqual(res["mode"], "set")
        body = codex_cfg.read_text()
        self.assertTrue(body.startswith("notify = ["))
        self.assertIn('\n[mcp_servers.sample]\ncommand = "python3"\n', body)
        self.assertFalse(pb.merge_codex_notify(codex_cfg, self.base / "panebell.py")["changed"])

    def test_ssh_host_setup_and_install_script_setup_flag(self):
        remote_home = self.base / "remote_home"
        remote_proj = remote_home / "Sandbox/paper-planes"
        remote_proj.mkdir(parents=True)
        (remote_home / ".claude").mkdir(parents=True)
        (remote_home / ".codex").mkdir(parents=True)
        pb.tmux("new-session", "-d", "-s", "sketchbook", "-c", str(remote_proj))
        pb.tmux("kill-session", "-t", "=anchor")

        fake_ssh = self.base / "fake_ssh"
        fake_ssh.write_text(
            "#!/usr/bin/env python3\n"
            "import os, subprocess, sys\n"
            "cmd = sys.argv[-1]\n"
            f"env = dict(os.environ, HOME={str(remote_home)!r}, PATH={os.environ['PATH']!r})\n"
            "sys.exit(subprocess.run(['/bin/sh', '-c', cmd], env=env).returncode)\n"
        )
        fake_ssh.chmod(0o755)

        mac_cfg = self.base / "mac_home/.config/panebell/config.json"
        with patch.object(pb, "SSH", [str(fake_ssh)]):
            ns = pb.argparse.Namespace(
                config=str(mac_cfg),
                helper_path="",
                ssh_host="studio-box",
                host_only=False,
                claude=None,
                codex=None,
                claude_settings="",
                codex_config="",
                dry_run=False,
                json=True,
            )
            rep = pb.run_setup(ns)
        self.assertEqual(rep["ssh_host"], "studio-box")
        saved_mac = json.loads(mac_cfg.read_text())
        self.assertEqual(saved_mac["sshHost"], "studio-box")
        self.assertEqual(saved_mac["projects"], [
            {"name": "paper-planes", "session": "sketchbook", "path": str(remote_proj)}
        ])
        self.assertTrue((remote_home / ".local/lib/panebell/panebell.py").is_file())
        self.assertTrue((remote_home / ".claude/settings.json").is_file())
        self.assertTrue((remote_home / ".codex/config.toml").is_file())

        # Exercise install.sh --setup end-to-end with a lightweight stub build.sh
        checkout = self.base / "checkout"
        (checkout / "scripts").mkdir(parents=True)
        root_dir = Path(pb.__file__).resolve().parent
        shutil.copy2(root_dir / "install.sh", checkout / "install.sh")
        shutil.copy2(root_dir / "scripts/install-host.sh", checkout / "scripts/install-host.sh")
        shutil.copy2(root_dir / "panebell.py", checkout / "panebell.py")
        stub_build = checkout / "scripts/build.sh"
        stub_build.write_text(
            '#!/bin/sh\nset -eu\nmkdir -p "$1/PaneBell.app/Contents/MacOS"\n'
            'printf "#!/bin/sh\\nexit 0\\n" > "$1/PaneBell.app/Contents/MacOS/PaneBell"\n'
            'chmod 755 "$1/PaneBell.app/Contents/MacOS/PaneBell"\n'
        )
        stub_build.chmod(0o755)
        local_home = self.base / "local_user"
        local_proj = local_home / "Sandbox/origami"
        local_proj.mkdir(parents=True)
        pb.tmux("new-session", "-d", "-s", "craft", "-c", str(local_proj))
        install_env = dict(
            os.environ,
            HOME=str(local_home),
            PANEBELL_APPS_DIR=str(local_home / "Applications"),
            PANEBELL_CONFIG=str(local_home / ".config/panebell/config.json"),
            PANEBELL_STATE_DIR=str(pb.BASE),
        )
        subprocess.run([str(checkout / "install.sh"), "--setup", "--claude", "--codex"], env=install_env, check=True)
        self.assertTrue((local_home / "Applications/PaneBell.app/Contents/MacOS/PaneBell").is_file())
        self.assertTrue((local_home / ".local/lib/panebell/panebell.py").is_file())
        local_cfg = json.loads((local_home / ".config/panebell/config.json").read_text())
        self.assertTrue(any(p["path"] == "~/Sandbox/origami" for p in local_cfg["projects"]))
        self.assertTrue((local_home / ".claude/settings.json").is_file())
        self.assertTrue((local_home / ".codex/config.toml").is_file())


if __name__ == "__main__":
    unittest.main()
