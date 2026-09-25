---
description: Local Polaris 2 (polaris-2) — server status, or a conductor/manager decision on a packet
argument-hint: status | decision <packet> | manager <packet>
allowed-tools: Bash(python3:*)
---

Arguments: `$ARGUMENTS`

- Empty or `status`: run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/autonoxis.py health`. On exit 2, say the server is down and give the start command from the `autonoxis` skill.
- `decision <packet>` or `manager <packet>`: run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/autonoxis.py conductor --track <decision|manager> --json` with the packet on stdin (heredoc, so quoting is safe).

Report the label, confidence and the ACT/ESCALATE verdict (exit 0 ACT, 3 ESCALATE, 2 error). On ESCALATE, do not act on the label; say it needs a frontier model or the human.
