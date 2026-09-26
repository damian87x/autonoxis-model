#!/usr/bin/env python3
"""autonoxis: CLI for the local autonoxis server running Polaris 3 (polaris-3) (Jev wire format, stdlib only).

  autonoxis.py health
  autonoxis.py conductor --track decision|manager (--packet TEXT | --file F | stdin) [--json] [-t 0.8]
      exit 0 ACT (confidence >= threshold) / 3 ESCALATE / 2 error
      -t can only raise the trained 0.8 gate; lower values are clamped to 0.8
  autonoxis.py ask --state JSON --questions FILE      generic /v1/systemone passthrough
  autonoxis.py gate -c "<criteria>" [--diff | --file F | stdin] [-p 0.7]
      exit 0 pass / 1 fail / 2 error  (noul gate: outside the fine-tune)

URL: $AUTONOXIS_URL, else $CLAUDE_PLUGIN_OPTION_URL (plugin userConfig), else http://127.0.0.1:8765.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

QUESTIONS = Path(__file__).resolve().parents[1] / "references" / "conductor-questions.json"
THRESHOLD = 0.8


def base_url():
    return (os.environ.get("AUTONOXIS_URL") or os.environ.get("CLAUDE_PLUGIN_OPTION_URL") or "http://127.0.0.1:8765").rstrip("/")


def die(msg):
    print(f"autonoxis: {msg}", file=sys.stderr)
    sys.exit(2)


def call(path, body=None, timeout=60):
    url = base_url() + path
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        die(f"HTTP {e.code} from {url}: {e.read().decode(errors='replace')[:300]}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        die(f"cannot reach {url} ({e}); start the server (see SKILL.md)")
    if not isinstance(res, dict):
        die(f"unexpected response from {url}: {json.dumps(res)[:300]}")
    res["_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return res


def read_file(path):
    try:
        return Path(path).read_text()
    except (OSError, ValueError) as e:
        die(f"cannot read {path}: {e}")


def unit(x, what, res):
    """A finite probability in [0, 1], else exit 2."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not 0.0 <= x <= 1.0:
        die(f"bad {what} {x!r} in response: {json.dumps(res)[:300]}")
    return float(x)


def read_input(a):
    if getattr(a, "packet", None) is not None:
        text = a.packet
    elif a.file:
        text = read_file(a.file)
    else:
        text = sys.stdin.read()
    if not text.strip():
        die("empty input")
    return text


def cmd_health(a):
    print(json.dumps(call("/health", timeout=5)))


def cmd_conductor(a):
    q = json.loads(QUESTIONS.read_text())[a.track]
    res = call("/v1/systemone", {"state": {"packet": read_input(a)}, "questions": {"label": q}})
    answers = res.get("answers")
    ans = answers.get("label") if isinstance(answers, dict) else None
    if not isinstance(ans, dict) or "choice" not in ans or "confidence" not in ans:
        die(f"no `label` answer in response: {json.dumps(res)[:300]}")
    if not isinstance(ans["choice"], str) or ans["choice"] not in q["criteria"]:
        die(f"choice {ans['choice']!r} is not a {a.track} label ({', '.join(q['criteria'])}): {json.dumps(res)[:300]}")
    conf = unit(ans["confidence"], "confidence", res)
    probs = ans.get("probabilities", {})
    if not isinstance(probs, dict) or not all(isinstance(v, (int, float)) for v in probs.values()):
        die(f"bad probabilities in response: {json.dumps(res)[:300]}")
    threshold = max(THRESHOLD, a.threshold)  # may only be raised above the trained gate
    action = "ACT" if conf >= threshold else "ESCALATE"
    out = {"track": a.track, "label": ans["choice"], "confidence": conf, "probabilities": probs,
           "action": action, "threshold": threshold, "model": res.get("model"), "ms": res["_ms"]}
    if a.json:
        print(json.dumps(out))
    else:
        probs = " ".join(f"{k}={v:.3f}" for k, v in out["probabilities"].items())
        print(f"{out['label']} confidence={conf:.3f} -> {action} (threshold {threshold})\n{probs}")
    sys.exit(0 if action == "ACT" else 3)


def load(arg):
    if arg == "-":
        return json.loads(sys.stdin.read())
    p = Path(arg[1:] if arg.startswith("@") else arg)
    return json.loads(read_file(p) if p.is_file() else arg)


def cmd_ask(a):
    try:
        body = {"state": load(a.state), "questions": load(a.questions)}
    except ValueError as e:
        die(f"bad JSON: {e}")
    print(json.dumps(call("/v1/systemone", body), indent=2))


def cmd_gate(a):
    if a.diff:
        state = subprocess.run(["git", "diff", "HEAD"], capture_output=True, text=True).stdout
    elif a.file:
        state = read_file(a.file)
    else:
        state = sys.stdin.read()
    if not state.strip():
        die("gate: empty state")
    q = {"gate": {"type": "noul", "instructions": "Does `content` satisfy this acceptance criterion: " + a.criteria}}
    res = call("/v1/systemone", {"state": {"content": state}, "questions": q})
    try:
        p = res["answers"]["gate"]["noul"]
    except (KeyError, TypeError):
        die(f"no `gate` noul in response: {json.dumps(res)[:300]}")
    p = unit(p, "gate noul", res)
    print(json.dumps({"passed": p >= a.p, "probability": p, "threshold": a.p, "ms": res["_ms"],
                      "note": "noul gate is outside the conductor fine-tune; treat as a hint"}))
    sys.exit(0 if p >= a.p else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("health").set_defaults(fn=cmd_health)
    s = sub.add_parser("conductor")
    s.add_argument("--track", choices=["decision", "manager"], required=True)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--packet")
    g.add_argument("--file")
    s.add_argument("--json", action="store_true")
    s.add_argument("-t", "--threshold", type=float, default=THRESHOLD, help="raise-only; values below 0.8 are clamped to 0.8")
    s.set_defaults(fn=cmd_conductor)
    s = sub.add_parser("ask")
    s.add_argument("--state", required=True, help="JSON, @file, file path, or - for stdin")
    s.add_argument("--questions", required=True, help="JSON, @file, or file path")
    s.set_defaults(fn=cmd_ask)
    s = sub.add_parser("gate")
    s.add_argument("-c", "--criteria", required=True)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--diff", action="store_true")
    g.add_argument("--file")
    s.add_argument("-p", type=float, default=0.7)
    s.set_defaults(fn=cmd_gate)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
