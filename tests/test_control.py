import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from iafetch import control, dashboard
from iafetch.config import parse_config
from iafetch.progress import process_start, status_path


class ControlTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        patch = mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.base)})
        patch.start()
        self.addCleanup(patch.stop)

    def fake_run(self):
        """A process standing in for a running get, with the status file it would write."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait()))
        status_path().parent.mkdir(parents=True, exist_ok=True)
        status_path().write_text(json.dumps({"pid": proc.pid, "pid_start": process_start(proc.pid), "state": "downloading",
                                             "identifier": "kagemusha", "title": "Kagemusha", "name": "Kagemusha.m4v",
                                             "file_done": 4 * 1024 ** 3, "file_size": 12 * 1024 ** 3}))
        return proc

    def test_nothing_to_stop(self):
        self.assertEqual(control.stop(), (False, "No iafetch get is running."))

    def test_stop_now(self):
        proc = self.fake_run()

        def kill(pid, sig):  # the run ends, as a get does on SIGTERM
            os.kill(pid, sig)
            proc.wait()
        ok, message = control.stop(kill=kill, sleep=lambda s: None)
        self.assertTrue(ok, message)
        self.assertEqual(message, "Stopped in Kagemusha, 4.0 of 12.0 GB of Kagemusha.m4v. iafetch start picks up where it left off.")

    def test_a_run_that_wont_stop(self):
        proc = self.fake_run()
        t = [0.0]
        ok, message = control.stop(kill=lambda pid, sig: None, sleep=lambda s: t.__setitem__(0, t[0] + s),
                                   clock=lambda: t[0], wait=3)
        self.assertFalse(ok)
        self.assertIn("hasn't stopped", message)

    def test_stop_after_the_current_item(self):
        proc = self.fake_run()
        ok, message = control.stop(after_current=True)
        self.assertIn("after Kagemusha is downloaded and filed", message)
        self.assertTrue(control.stop_after_requested(proc.pid))
        self.assertFalse(control.stop_after_requested(proc.pid + 1))  # another run isn't affected
        self.assertIsNone(proc.poll())  # still running


class DashboardButtonsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp.name})
        patch.start()
        self.addCleanup(patch.stop)
        self.config = parse_config({"root": tmp.name})

    def serve(self, writes):
        self.started = []
        dash = dashboard.Dashboard(self.config, writes=writes, starter=lambda: (self.started.append(1), (True, "Downloading."))[1])
        server = dash.server("127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return dash, f"http://127.0.0.1:{server.server_address[1]}"

    def post(self, url, token="", body=b"{}"):
        request = urllib.request.Request(url, data=body, method="POST",
                                         headers={"X-Iafetch-Token": token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read())

    def test_start_and_stop_with_the_pages_token(self):
        dash, base = self.serve(writes=True)
        with urllib.request.urlopen(base + "/") as r:
            self.assertIn(json.dumps(dash.token).encode(), r.read())
        self.assertEqual(self.post(base + "/api/start", dash.token), (200, {"ok": True, "message": "Downloading."}))
        self.assertEqual(self.started, [1])
        self.assertEqual(self.post(base + "/api/stop", dash.token)[1]["message"], "No iafetch get is running.")

    def test_refused_without_the_token_or_on_the_home_network(self):
        dash, base = self.serve(writes=True)
        self.assertEqual(self.post(base + "/api/start", "wrong")[0], 403)
        lan, lan_base = self.serve(writes=False)
        self.assertEqual(self.post(lan_base + "/api/start", lan.token)[0], 403)
        with urllib.request.urlopen(lan_base + "/") as r:
            self.assertNotIn(lan.token.encode(), r.read())
        self.assertEqual(self.started, [])


if __name__ == "__main__":
    unittest.main()
