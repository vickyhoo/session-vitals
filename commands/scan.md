---
description: Scan every Claude Code session and rank them by compaction count
---

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/vitals.py" scan
```

Show the output as-is. Do not reformat the table.

If any session is at 🔴, add one clarifying line: the compaction count is a descriptive
metric showing how much conversation has been summarized away. It is not a verdict that
something is wrong with the model. Whether to retire a session is the user's call.
