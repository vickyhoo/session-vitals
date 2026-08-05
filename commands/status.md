---
description: Report on the current session - how much has been compacted away, whether a checkpoint exists, and what to do next
---

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/vitals.py" status
```

Show the output to the user as it is printed. It is already written for them; do not
restate it line by line.

Add at most two sentences of your own, and only if you know something the command does
not. You do have context it lacks - what is half-finished right now, whether the last
hour went into one hard problem or twenty small ones. That is worth saying. The byte
counts are not.

If the output ends in an arrow, that is the recommended action. Offer to do it rather
than describing it.
