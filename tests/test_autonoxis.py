"""Stdlib tests: a fake /v1/systemone server in-process, the CLI run as a subprocess."""
import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "autonoxis.py"
MANAGER = {"ACCEPT", "VERIFY", "REJECT", "REOPEN", "ESCALATE"}


class Fake:
    """Records every POST body; replies with the configured answer for question `label` / `gate`."""

    def __init__(self):
        self.bodies, self.reply, self.status, self.raw = [], {}, 200, None
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/health":
                    return self._send(200, {"status": "ok", "model": "polaris-1"})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.bodies.append((self.path, body))
                if fake.status != 200:
                    return self._send(fake.status, {"error": "boom"})
                if fake.raw is not None:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(fake.raw)))
                    self.end_headers()
                    return self.wfile.write(fake.raw)
                self._send(200, {"model": "polaris-1", "answers": fake.reply, "usage": {"input_tokens": 10, "output_tokens": 0}})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def choice(label, conf, probs):
    return {"label": {"type": "choice", "choice": label, "confidence": conf, "probabilities": probs}}


class T(unittest.TestCase):
    def setUp(self):
        self.fake = Fake()

    def tearDown(self):
        self.fake.close()

    def run_cli(self, *args, stdin="", url=None):
        env = {k: v for k, v in os.environ.items() if k not in ("AUTONOXIS_URL", "CLAUDE_PLUGIN_OPTION_URL")}
        env["AUTONOXIS_URL"] = url or self.fake.url
        return subprocess.run([sys.executable, str(CLI), *args], input=stdin, capture_output=True, text=True, env=env, timeout=30)

    def test_decision_request_shape_and_act(self):
        self.fake.reply = choice("DISPATCH", 0.93, {"STOP": 0.02, "ASK": 0.05, "DISPATCH": 0.93})
        r = self.run_cli("conductor", "--track", "decision", "--packet", "lane L1 green, reviewer free")
        self.assertEqual(r.returncode, 0, r.stderr)
        path, body = self.fake.bodies[-1]
        self.assertEqual(path, "/v1/systemone")
        self.assertEqual(body["state"], {"packet": "lane L1 green, reviewer free"})
        self.assertEqual(list(body["questions"]), ["label"])
        q = body["questions"]["label"]
        self.assertEqual(q["type"], "choice")
        self.assertEqual(set(q["criteria"]), {"STOP", "ASK", "DISPATCH"})
        self.assertTrue(q["instructions"].startswith("`packet`"))
        self.assertIn("DISPATCH", r.stdout)
        self.assertIn("ACT", r.stdout)

    def test_manager_escalates_below_threshold(self):
        self.fake.reply = choice("VERIFY", 0.55, {"ACCEPT": 0.1, "VERIFY": 0.55, "REJECT": 0.2, "REOPEN": 0.1, "ESCALATE": 0.05})
        r = self.run_cli("conductor", "--track", "manager", "--json", stdin="evidence stale")
        self.assertEqual(r.returncode, 3, r.stderr)
        _, body = self.fake.bodies[-1]
        self.assertEqual(body["state"], {"packet": "evidence stale"})
        self.assertEqual(set(body["questions"]["label"]["criteria"]), MANAGER)
        out = json.loads(r.stdout)
        self.assertEqual(out["label"], "VERIFY")
        self.assertEqual(out["action"], "ESCALATE")
        self.assertEqual(out["threshold"], 0.8)

    def test_packet_from_file(self):
        self.fake.reply = choice("STOP", 0.8, {"STOP": 0.8, "ASK": 0.1, "DISPATCH": 0.1})
        f = Path(os.environ.get("TMPDIR", "/tmp")) / f"autonoxis-packet-{os.getpid()}.txt"
        f.write_text("no authority")
        try:
            r = self.run_cli("conductor", "--track", "decision", "--file", str(f))
        finally:
            f.unlink()
        self.assertEqual(r.returncode, 0, r.stderr)  # 0.8 is inclusive
        self.assertEqual(self.fake.bodies[-1][1]["state"], {"packet": "no authority"})

    def test_errors_exit_2(self):
        self.fake.status = 500
        r = self.run_cli("conductor", "--track", "decision", "--packet", "x")
        self.assertEqual(r.returncode, 2)
        r = self.run_cli("conductor", "--track", "decision", "--packet", "x", url="http://127.0.0.1:9")
        self.assertEqual(r.returncode, 2)
        r = self.run_cli("conductor", "--track", "decision", stdin="   ")
        self.assertEqual(r.returncode, 2)

    def test_health_and_url_override(self):
        r = self.run_cli("health")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("polaris-1", r.stdout)
        r = self.run_cli("health", url="http://127.0.0.1:9")
        self.assertEqual(r.returncode, 2)

    def test_gate_exit_codes(self):
        self.fake.reply = {"gate": {"type": "noul", "noul": 0.9}}
        r = self.run_cli("gate", "-c", "Zero test failures", stdin="10 passed")
        self.assertEqual(r.returncode, 0, r.stderr)
        _, body = self.fake.bodies[-1]
        self.assertEqual(body["state"], {"content": "10 passed"})
        self.assertEqual(body["questions"]["gate"]["type"], "noul")
        self.assertIn("Zero test failures", body["questions"]["gate"]["instructions"])
        self.fake.reply = {"gate": {"type": "noul", "noul": 0.3}}
        self.assertEqual(self.run_cli("gate", "-c", "x", stdin="y").returncode, 1)
        self.assertEqual(self.run_cli("gate", "-c", "x", "-p", "0.2", stdin="y").returncode, 0)
        self.assertEqual(self.run_cli("gate", "-c", "x", stdin="").returncode, 2)

    def test_ask_passthrough(self):
        self.fake.reply = {"q": {"type": "noul", "noul": 0.7}}
        r = self.run_cli("ask", "--state", '{"a": 1}', "--questions", str(ROOT / "references" / "conductor-questions.json"))
        self.assertEqual(r.returncode, 0, r.stderr)
        _, body = self.fake.bodies[-1]
        self.assertEqual(body["state"], {"a": 1})
        self.assertEqual(set(body["questions"]), {"decision", "manager"})
        self.assertEqual(json.loads(r.stdout)["answers"]["q"]["noul"], 0.7)

    def test_threshold_can_only_be_raised(self):
        self.fake.reply = choice("DISPATCH", 0.2, {"STOP": 0.3, "ASK": 0.5, "DISPATCH": 0.2})
        r = self.run_cli("conductor", "--track", "decision", "--packet", "x", "-t", "0.1", "--json")
        self.assertEqual(r.returncode, 3, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["action"], "ESCALATE")
        self.assertEqual(out["threshold"], 0.8)
        self.fake.reply = choice("DISPATCH", 0.85, {"STOP": 0.05, "ASK": 0.1, "DISPATCH": 0.85})
        r = self.run_cli("conductor", "--track", "decision", "--packet", "x", "-t", "0.9", "--json")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(json.loads(r.stdout)["action"], "ESCALATE")

    def assertCleanError(self, r):
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("autonoxis:", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_conductor_bad_confidence_exit_2(self):
        for conf in (None, "high", "0.9x", [0.9], float("inf"), 1.5, -0.1, True):
            with self.subTest(conf=conf):
                reply = choice("DISPATCH", conf, {"DISPATCH": 0.9})
                if conf is None:
                    del reply["label"]["confidence"]
                self.fake.reply = reply
                self.assertCleanError(self.run_cli("conductor", "--track", "decision", "--packet", "x"))

    def test_conductor_bad_response_shapes_exit_2(self):
        for reply in ({}, {"label": "DISPATCH"}, {"label": {"confidence": 0.9}}, [1, 2]):
            with self.subTest(reply=reply):
                self.fake.reply = reply
                self.assertCleanError(self.run_cli("conductor", "--track", "decision", "--packet", "x"))

    def test_conductor_choice_outside_track_exit_2(self):
        # a manager-only label on the decision track, and an unknown label, must never ACT
        for track, label in (("decision", "ACCEPT"), ("manager", "DISPATCH"), ("decision", "MAYBE"), ("decision", 1)):
            with self.subTest(track=track, label=label):
                self.fake.reply = choice(label, 0.99, {"STOP": 0.01})
                r = self.run_cli("conductor", "--track", track, "--packet", "x", "--json")
                self.assertCleanError(r)
                self.assertEqual(r.stdout, "")

    def test_conductor_valid_choice_unchanged(self):
        self.fake.reply = choice("ACCEPT", 0.99, {"ACCEPT": 0.99, "VERIFY": 0.01})
        r = self.run_cli("conductor", "--track", "manager", "--packet", "x", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["label"], "ACCEPT")

    def test_file_read_errors_exit_2(self):
        missing = "/nonexistent/autonoxis-packet.txt"
        self.assertCleanError(self.run_cli("conductor", "--track", "decision", "--file", missing))
        self.assertCleanError(self.run_cli("gate", "-c", "x", "--file", missing))
        self.assertCleanError(self.run_cli("conductor", "--track", "decision", "--file", str(ROOT)))
        self.assertCleanError(self.run_cli("gate", "-c", "x", "--file", str(ROOT)))
        f = Path(os.environ.get("TMPDIR", "/tmp")) / f"autonoxis-bin-{os.getpid()}.txt"
        f.write_bytes(b"\xff\xfe\x00bad")
        try:
            self.assertCleanError(self.run_cli("conductor", "--track", "decision", "--file", str(f)))
            self.assertCleanError(self.run_cli("gate", "-c", "x", "--file", str(f)))
            self.assertCleanError(self.run_cli("ask", "--state", "@" + str(f), "--questions", "{}"))
        finally:
            f.unlink()

    def test_non_json_response_exit_2(self):
        self.fake.raw = b"not json"
        self.assertCleanError(self.run_cli("conductor", "--track", "decision", "--packet", "x"))
        self.assertCleanError(self.run_cli("gate", "-c", "x", stdin="y"))
        self.assertCleanError(self.run_cli("ask", "--state", "{}", "--questions", "{}"))

    def test_http_and_connection_errors_exit_2_all_commands(self):
        for url in (None, "http://127.0.0.1:9"):
            self.fake.status = 500
            with self.subTest(url=url):
                self.assertCleanError(self.run_cli("conductor", "--track", "decision", "--packet", "x", url=url))
                self.assertCleanError(self.run_cli("gate", "-c", "x", stdin="y", url=url))
                self.assertCleanError(self.run_cli("ask", "--state", "{}", "--questions", "{}", url=url))

    def test_gate_bad_probability_exit_2(self):
        for noul in ("high", None, float("nan"), 2.0):
            with self.subTest(noul=noul):
                self.fake.reply = {"gate": {"type": "noul", "noul": noul}}
                self.assertCleanError(self.run_cli("gate", "-c", "x", stdin="y"))

    def test_ask_bad_json_exit_2(self):
        self.assertCleanError(self.run_cli("ask", "--state", "{not json", "--questions", "{}"))
        self.assertCleanError(self.run_cli("ask", "--state", "-", "--questions", "{}", stdin="nope"))


if __name__ == "__main__":
    unittest.main()
