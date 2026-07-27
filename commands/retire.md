---
description: Capture the current session's progress and print the steps to continue in a fresh session
---

First run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/vitals.py" retire
```

Then **do the part that actually matters**: write down your current understanding of
this project.

The script can only print a checklist. It cannot write this part, because only you have
the full context. Cover:

- What is being worked on and how far it got
- Why the current approach was chosen, and what was ruled out
- Decisions already settled that should not be reopened
- The concrete next step
- Any traps or open questions

Write conclusions, not a log of the conversation. If the user has PROGRESS.md enabled
(see `~/.session-vitals/config.json`), write into the project root's PROGRESS.md and
update only the block belonging to this session. Otherwise print it in the conversation
and let the user decide where it goes.

**Never move or delete a transcript file.** The running process is still writing to it.
