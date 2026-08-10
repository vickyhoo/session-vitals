---
description: Save what this session has concluded into the project's PROGRESS.md, without ending the session
---

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/vitals.py" checkpoint
```

If it cannot tell which project this session belongs to, re-run it with the directory
you are actually working in. Do not guess from the shell's location - that follows the
last command you ran, not the project.

Then write your current understanding and pipe it into the command it printed. Cover:

- What is being worked on and how far it got
- Why the current approach was chosen, and what was ruled out
- Decisions already settled that should not be reopened
- The concrete next step
- Any traps or open questions

Write conclusions, not a log of the conversation. Someone picking this up tomorrow
should learn where things stand, not what happened in what order.

**Do not write PROGRESS.md with the Write tool.** It looks equivalent and is not: the
command scans for credentials before writing, enforces a size limit, takes a file lock,
refuses to write into a home directory, and replaces only the block belonging to this
session. A hand-written file carries no session markers, so the next session cannot
update its own block and the file only ever grows.

If the command exits non-zero, say so in one plain sentence and quote the error. Do not
substitute the Write tool and do not improvise another way to record it.

This replaces the block if this session already wrote one, so running it repeatedly is
safe and is the intended use - checkpoint whenever a decision settles, not only at the
end. To checkpoint *and* hand off to a fresh session, use `/session-vitals:retire`.

If the project has no PROGRESS.md yet, this is what creates it - and creating it is what
opts the project into automatic updates after each compaction. Say so once, plainly, the
first time: it is a new file in their repository that will probably get committed.
