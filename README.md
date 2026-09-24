# autonoxis-model

A [Claude Code](https://claude.com/claude-code) plugin that asks **Polaris 1** (`polaris-1`), a small local decision model, for the next action of an autonomous coding lane:

- `decision` track: `STOP | ASK | DISPATCH`
- `manager` track: `ACCEPT | VERIFY | REJECT | REOPEN | ESCALATE`

Polaris 1 is a LoRA adapter on [Bespoke-Nimble-9B](https://huggingface.co/bespokelabs/Bespoke-Nimble-9B) (built on Qwen3.5-9B). It does not generate text: it scores every allowed label and returns the top one with a confidence. It runs on your own GPU behind the autonoxis server, with no API key and no per-call cost (about 100 ms per decision after warm-up). The plugin acts only when confidence is at least 0.8 and escalates everything else.

## Links

- Polaris 1 model: https://huggingface.co/damianborek/polaris-1
- Vega 1 model (earlier, generative): https://huggingface.co/damianborek/vega-1
- Pi plugin + server: https://github.com/damian87x/pi-autonoxis-model

## Install

In Claude Code:

```
/plugin marketplace add damian87x/autonoxis-model
/plugin install autonoxis-model@autonoxis-model
```

From a shell:

```bash
claude plugin marketplace add damian87x/autonoxis-model
claude plugin install autonoxis-model@autonoxis-model
```

The plugin is a client only. It needs the autonoxis server running (next section).

## Run the server

The server is `server.py` in [damian87x/pi-autonoxis-model/server](https://github.com/damian87x/pi-autonoxis-model/tree/main/server). Its README lists the requirements (an NVIDIA GPU with about 20 GB for the 9B model in bf16; Python with torch, transformers, peft, huggingface_hub) and the full setup. In short:

```bash
git clone https://github.com/damian87x/pi-autonoxis-model && cd pi-autonoxis-model
git clone https://github.com/bespokelabsai/nimble server/nimble
hf download bespokelabs/Bespoke-Nimble-9B --revision 594dfdcfb6f94e3d0c0db7535180d3c71689169a --local-dir base
hf download damianborek/polaris-1 --local-dir adapter
echo '{"model_path": "base", "max_input_tokens": 2048}' > nimble-model.json
python server/server.py --model-config nimble-model.json --adapter adapter --port 8765
```

It binds to `127.0.0.1` only and has no authentication. The plugin finds it at `AUTONOXIS_URL`, else the plugin option `url`, else `http://127.0.0.1:8765`.

## Usage

Slash command:

```
/autonoxis-model:autonoxis status
/autonoxis-model:autonoxis decision <packet>
/autonoxis-model:autonoxis manager <packet>
```

The `autonoxis` skill tells Claude when to use the model and how. The CLI behind both (`scripts/autonoxis.py`, stdlib only) also works on its own:

```bash
python3 scripts/autonoxis.py health
python3 scripts/autonoxis.py conductor --track decision --packet "<condensed lane packet>"
python3 scripts/autonoxis.py conductor --track manager --file packet.txt --json
python3 scripts/autonoxis.py ask --state '{"packet":"..."}' --questions q.json   # raw /v1/systemone passthrough
npm test 2>&1 | python3 scripts/autonoxis.py gate -c "Zero test failures" -p 0.7
```

`conductor` exit codes:

- `0` ACT: confidence >= 0.8, use the label.
- `3` ESCALATE: confidence below 0.8, hand the decision to a stronger model or a human.
- `2` error: server down, HTTP error, bad or missing confidence, a label from the wrong track, unreadable file or empty input. Never act on an error.

`-t` can only raise the 0.8 gate; lower values are clamped to 0.8. `gate` exits 0 pass, 1 fail, 2 error.

The model was trained on one call shape: one question with id `label`, `state = {"packet": text}`, and the question text in [`references/conductor-questions.json`](references/conductor-questions.json). `conductor` sends exactly that. With `ask`, keep the same shape or accuracy is unknown.
The decision track uses the conductor contract prompt; on real-world orchestrator packets (v9) accuracy rises from 59% to 67% with no change on v5–v8.

## Honest limits

See the [Polaris 1 model card](https://huggingface.co/damianborek/polaris-1) for the full evaluation.

- 59.2 ± 0.9 of 60 on a held-out set (v8) across 10 seeds, but v8 is in-distribution: it shares scenario families with the training data. Other domains and raw transcripts are not measured.
- The 0.8 gate is not a guarantee. The released adapter makes one confident miss on v8 (`ACCEPT` expected, `VERIFY` given at 0.998).
- Input is limited to 2048 tokens. Send a condensed packet, not a transcript.
- `gate` asks a yes/no question outside the fine-tune. Treat it as a hint, never the only guard before an irreversible action.
- The packet is not treated as hostile; injected text can move the answer. Put hard vetoes in code first.

## Tests

```bash
python3 -m unittest discover -s tests -v   # offline, against a fake in-process server
claude plugin validate .
```

## License

MIT for this plugin. The Polaris 1 adapter is Apache-2.0; see its model card.
