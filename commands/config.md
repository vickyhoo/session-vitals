---
description: Show or change session-vitals settings
argument-hint: [set|get|unset] [key] [value]
---

$ARGUMENTS

If arguments were passed above, run them through the command directly rather than
asking the user to restate them. With no arguments, show everything:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/vitals.py" config
```

To change one:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/vitals.py" config set progress_md.enabled true
python3 "${CLAUDE_PLUGIN_ROOT}/vitals.py" config get progress_md.enabled
python3 "${CLAUDE_PLUGIN_ROOT}/vitals.py" config unset progress_md.enabled
```

Show the listing as printed. `set` marks values the user has changed; everything else is
a default, and the listing says what each one does.

If the user describes what they want rather than naming a key, pick the key yourself and
run it - the names are in the listing. Do not edit `~/.session-vitals/config.json`
directly: the command validates the key against the known settings and coerces the value
to the declared type, and a key it does not recognize is rejected rather than written.
A typo written by hand takes effect never and says nothing.

Two settings deserve a sentence when they come up:

- `progress_md.enabled` turns checkpointing on globally, but it never creates a file.
  A project opts in when its `PROGRESS.md` first exists - `/session-vitals:checkpoint`
  creates it. So enabling this does not touch any repository on its own.
- `update_check` is the only thing that makes a network request. Off means the plugin
  makes none at all.
