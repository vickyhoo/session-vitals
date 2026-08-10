---
description: Check whether session-vitals is actually working — runtime, which copy is running, version, hook wiring, and whether hooks are live in this session
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
- **No hook has fired in THIS session** — the session almost certainly started before
  the plugin was installed. Hook configuration is captured at session start, so nothing
  here is wired and nothing happens automatically. Tell the user to restart the session.
- **No compaction markers** — Claude Code's transcript field may have changed. Counts
  are unreliable until the plugin is updated; do not trust the current numbers.
- **A different copy is registered as installed** — the file being run is not the one
  the plugin manager recorded. Harmless if intentional; if not, the behavior on screen
  may not match the code being edited.
- **Desktop notifications unavailable** — expected off macOS. Everything else works.
