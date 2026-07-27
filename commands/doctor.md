---
description: Check whether session-vitals is actually working (runtime, hook wiring, heartbeat, transcript format)
---

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/vitals.py" doctor
```

Show the output as-is.

Why this command exists: hook failures are silent. A hook reading the wrong field name
can do nothing for months without a single sign. Explain what each failure means:

- **No heartbeat recorded** — hooks are not wired up, or that event has not fired yet.
  Have the user confirm the plugin is installed and that a session resume or compaction
  has happened at least once.
- **No compaction markers** — Claude Code's transcript field may have changed. Counts
  are unreliable until the plugin is updated; do not trust the current numbers.
- **Approval dialog unavailable** — expected on non-macOS platforms. Diagnostics and
  checkpointing are unaffected.
