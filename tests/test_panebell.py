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


if __name__ == "__main__":
    unittest.main()
