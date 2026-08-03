---
description: Capture the current session's progress and print the steps to continue in a fresh session
---

First run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/vitals.py" retire
```

It prints a checklist whose first step is a ready-to-run `write-progress` command with
the project directory already resolved.

Then **do the part that actually matters**: write down your current understanding of
this project, and save it *through that command*.

The script can only print a checklist. It cannot write this part, because only you have
the full context. Cover:

- What is being worked on and how far it got
- Why the current approach was chosen, and what was ruled out
- Decisions already settled that should not be reopened
- The concrete next step
- Any traps or open questions

Write conclusions, not a log of the conversation.

**Do not write PROGRESS.md with the Write tool.** It looks equivalent and is not: the
command scans for credentials before writing, enforces a size limit, takes a file lock,
refuses to write into a home directory, and - the one that bites later - replaces only
the block belonging to this session. A hand-written file carries no session markers, so
the next session cannot update its own block and the file only ever grows.

If the command exits non-zero, say so in one plain sentence and quote the error. Do not
substitute the Write tool and do not improvise another way to record it.

If PROGRESS.md is not enabled (`~/.session-vitals/config.json`) the command says so. Then
print the summary in the conversation and let the user decide where it goes.

**Never move or delete a transcript file.** The running process is still writing to it.
