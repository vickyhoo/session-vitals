# Project progress

_Maintained by session-vitals, one block per session._

<!-- session-vitals:4ea67777-a62a-4c2b-b5ad-a76fab36 -->
_updated 2026-08-05T07:23:09+00:00_

## session-vitals — plugin development

### Where things stand
Working, installed, and dogfooding itself. Two jobs: report how often a session has been
compacted, and carry a checkpoint across compaction into the next session. 66 tests pass.
Nothing has been pushed to a remote yet — `git remote` is empty by choice.

Commands: `status` (this session in detail), `scan` (all sessions ranked), `checkpoint`
(save now), `retire` (save + handoff), `doctor` (self check), plus `write-progress` and
`update-check` as plumbing.

### Settled — do not reopen

**The checkpoint prompt lives on SessionStart with `source: compact`.** Not PostCompact:
that event has no decision control, its output never reaches the model, and emitting
`hookSpecificOutput` there fails schema validation. Not PreCompact either: it cannot
inject, and anything injected is the first thing compaction discards. PostCompact is kept
for its heartbeat alone.

**Context injection does not grant a turn.** After `/compact` the session idles; the
checkpoint gets written on the user's next message. Verified on a real session.

**The project directory comes from every `cwd` the transcript recorded, not the current
one.** Two filters run first: paths are cut at the first vendor segment (`node_modules`
etc.), and any directory that is a strict ancestor of another recorded one is dropped —
that is what a launch directory looks like. Siblings are deliberately kept, which is what
makes a two-repo workspace resolve to the workspace. If the ancestor is home, a top-level
path, or the session wandered, it refuses rather than guesses.

**`$CLAUDE_PLUGIN_ROOT` is exported to hook processes only.** It is NOT set for Bash tool
calls, so any command the model is told to run must carry an absolute path resolved from
`__file__`. `CLAUDE_CODE_SESSION_ID` *is* set for Bash calls — that is how the commands
find their own transcript.

**Placeholders expand in skill/command content and hook commands, but not in hook runtime
output.** `commands/*.md` may use `${CLAUDE_PLUGIN_ROOT}`; injected `additionalContext`
may not.

**The dangerous-command gate was removed, not disabled.** `bypassPermissions` still honors
explicit `ask` rules plus a built-in `rm -rf /` circuit breaker, so the premise was wrong;
native rules also match compound subcommands and strip wrappers. Shipping a weaker
duplicate of a built-in safety mechanism invites false confidence. README documents the
native `ask` rules instead.

**`checkpoint` and `retire` share one command-line builder.** They drifted apart once
already and retire ended up telling the model to use the Write tool, bypassing every
safeguard.

**Update checking asks the source repo directly**, since the install path is
version-namespaced and the version is pinned by hand. Requires bumping `version` on every
release. Every failure is silence.

### Traps
- Hook configuration is snapshotted at session start. New hooks do not reach running
  sessions; removed subcommands still get called by them (`pretooluse` is kept as a no-op
  for this reason). `doctor` detects a session older than the install.
- `installed_plugins.json` points at a frozen copy under `~/.claude/plugins/cache/`. The
  directory-source marketplace serves the working tree live; `doctor` prints which file
  actually runs.
- `docs/designs/SESSION-VITALS.md` contains real client project names. Replace them with
  placeholders before any public push.

### Next
Nothing is blocked. Open items: publish the repo (then verify `update-check` against a
real remote — currently untested and reporting `UNKNOWN`), and the deferred E1 notch
integration / E4 trends work in TODOS.md.
<!-- /session-vitals:4ea67777-a62a-4c2b-b5ad-a76fab36 -->

