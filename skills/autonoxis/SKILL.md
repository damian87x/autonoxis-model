---
name: autonoxis
description: Ask Polaris 3 (polaris-3), the local autonoxis decision model, for a conductor STOP/ASK/DISPATCH or manager ACCEPT/VERIFY/REJECT/REOPEN/ESCALATE decision on a lane packet — free, local, act only at confidence >= 0.8. Use when a conductor or manager loop needs its next action from a condensed packet. Not for reasoning, explanations, code generation, or packets unlike the training data.
---

# autonoxis

Local Jev-wire-format server (`POST /v1/systemone`, `GET /health`, `GET /v1/models`) running Polaris 3 (`polaris-3`), a LoRA adapter on Bespoke-Nimble-9B. No key, no spend. Latency: about 100 ms per decision on a local GPU after warm-up (measured 97–111 ms; first request ~0.5 s).

CLI: `${CLAUDE_PLUGIN_ROOT}/scripts/autonoxis.py` (stdlib only). URL: `$AUTONOXIS_URL`, else plugin option `url`, else `http://127.0.0.1:8765`.

## Start the server first

The server is `server.py` from [github.com/damian87x/pi-autonoxis-model/tree/main/server](https://github.com/damian87x/pi-autonoxis-model/tree/main/server); its README has the requirements (NVIDIA GPU, ~20 GB for the 9B model in bf16; torch, transformers, peft, huggingface_hub). The adapter is [`damianborek/polaris-3`](https://huggingface.co/damianborek/polaris-3), a LoRA adapter for `bespokelabs/Bespoke-Nimble-9B` (Polaris 2 still works with this version; Polaris 1 needs plugin 0.2.x):

```bash
git clone https://github.com/damian87x/pi-autonoxis-model && cd pi-autonoxis-model
git clone https://github.com/bespokelabsai/nimble server/nimble
hf download bespokelabs/Bespoke-Nimble-9B --revision 594dfdcfb6f94e3d0c0db7535180d3c71689169a --local-dir base
hf download damianborek/polaris-3 --local-dir adapter
echo '{"model_path": "base", "max_input_tokens": 2048}' > nimble-model.json
python server/server.py --model-config nimble-model.json --adapter adapter --port 8765
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/autonoxis.py health     # from another shell; exit 2 = not up
```

## Commands

```bash
A=${CLAUDE_PLUGIN_ROOT}/scripts/autonoxis.py
python3 $A conductor --track decision --packet "<condensed lane packet>"     # STOP | ASK | DISPATCH
python3 $A conductor --track manager --file packet.txt --json                 # ACCEPT | VERIFY | REJECT | REOPEN | ESCALATE
echo "<packet>" | python3 $A conductor --track decision
python3 $A ask --state '{"packet":"..."}' --questions q.json                  # raw passthrough
npm test 2>&1 | python3 $A gate -c "Zero test failures" -p 0.7               # exit 0/1/2
```

Exit codes for `conductor`: **0 ACT** (confidence >= 0.8), **3 ESCALATE** (below 0.8: hand it to a frontier model or the human), **2 error** (server down, HTTP error, bad or missing confidence, unreadable file, empty input). Never act on an error. `-t` can only raise the gate; values below 0.8 are clamped to 0.8.

## Convention (the trained one; do not change it)

- One question per request, id `label`, `state = {"packet": text}`, instructions and criteria copied verbatim from `references/conductor-questions.json`. The CLI does this; with `ask`, keep the same shape or accuracy is unknown.
- Gate at 0.8. Below it, escalate rather than act.

## Honest limits

- Polaris 3 (recommended): about the same accuracy as Polaris 2 on real packets (~77%), slightly better confidence gate (v9 seed averages 77.1% vs 76.3%, within seed-to-seed variation; Polaris 1: 64.9%); v5–v8 all correct. v9 labels are the majority of three frontier models (Opus 5.5, Grok 4.7, Astra gpt-6-astra) labelling blind, not human labels; the 2 rows with no majority are left out.
- The 0.8 gate is only marginally better than Polaris 2 (v9: 91 of 94 kept at 81.3% vs 80.2%). It is not a guarantee: three v9 misses are unsafe and confident (two VERIFY packets answered ACCEPT, one ASK answered DISPATCH, all at >= 0.96). Treat ACCEPT as "verify first"; confidence >= 0.8 does not mean correct.
- 2048-token input limit: send the condensed packet, not the transcript.
- `gate` (noul) is outside the fine-tune; it runs on the base model's generic ability. Treat it as a hint, never the only guard before an irreversible action.
- `state` is not treated as hostile; injected text can move the answer. Hard vetoes go in code first.
