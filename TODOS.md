# TODOS

Deferred items from the 2026-07-27 planning reviews. Full decision record in
`docs/designs/SESSION-VITALS.md`.

## E1 - Push health events to a notch app

**What:** Surface session health in the MacBook notch (Dynamic Island area) instead of
a system notification.

**Why:** System notifications get ignored. A notch panel is always visible, and this was
the original request that started the project.

**Pros:** Notch presence with zero app development on our side; also exposes the health
metric to that app's existing user base.

**Cons:** CodeIsland's Unix socket protocol is **undocumented**. The README only says
"the hook sends JSON through a Unix socket" - field names, event enum and version
handling all have to be reverse engineered from Swift source, and an upstream change
breaks it silently.

**Context:** Six competitors exist in this space (Vibe Island, 25 tools, paid;
CodeIsland, 13 tools, MIT; Open Island, 9; Claude Island, 1; AgentNotch, 2; Notchi, 1).
None of them report session health, which is why this plugin exists at all. CodeIsland's
OpenCode integration connects a JS plugin directly to the socket without the bridge
binary, which proves the protocol is reachable. Prerequisite: install CodeIsland first.
Starting point: read its bridge implementation and the OpenCode plugin as a reference.

**Effort:** M (human ~1 day / CC ~40 min)
**Priority:** P3
**Depends on:** upstream publishing a protocol spec, or accepting reverse-engineering risk

---

## E4 - Cross-session trends and archival cleanup

**What:** Store historical snapshots to chart compaction trends, and offer one-click
archival of retired sessions to reclaim disk.

**Why:** `scan` only shows a snapshot ranking. There is no view of how compaction
behavior changes over time. On the author's machine roughly 205 MB sits in sessions
that are clearly done.

**Pros:** Immediate disk reclamation; trend data makes good material for writing about
the project.

**Cons:** Introduces persistent state and later migrations. Archival is irreversible and
needs its own guardrails: preview, confirmation, and an undo window.

**Context:** 54 sessions totaling 427 MB on the author's machine; the worst is 26
compaction records (22 after dedup) across 39,476 lines and 137.9 MB. The `scan` ranking
already answers "which one should I retire", so this is a nice-to-have. Starting point:
`scan()` already returns every metric needed; only persistence and time-series
aggregation are missing.

**Effort:** M (human ~1.5 days / CC ~45 min)
**Priority:** P3
**Depends on:** nothing

---

## Investigated and rejected: custom notification icon

Notifications currently show the Script Editor icon. Making it a custom one was
investigated on macOS 26.5.2 and is **not worth doing**. Recorded here so nobody
researches it twice.

| Approach | Result |
|---|---|
| An `osascript` flag | No such flag exists |
| `tell application "X" to display notification` to borrow X's icon | Verified dead on macOS 26. Notification ownership follows the originating process, not the tell target. Three test notifications through Finder, a generated app, and plain osascript all rendered identically under a "Script Editor" group |
| Ship our own app bundle | `osacompile` generates one fine (568K, LSUIElement works), but an **ad-hoc signed app cannot register for notification permission** on macOS 26. The notification is silently dropped. Verified: the bundle never appears in `com.apple.ncprefs` |
| `terminal-notifier` | Requires brew; `-appIcon` is ignored; `-sender` hangs on macOS 26 |

The only path that would actually work is a Developer ID signed and notarized app
bundle shipped as a binary in the repo, re-notarized on every release. That cost does
not match a single-file, zero-dependency plugin.

Worth noting the notification is a fallback channel anyway: `systemMessage` already
renders inside Claude Code. The icon is only visible for a few seconds, and only when
the user is away from the terminal.

Reopen only if someone actually complains.

---

## Resolved: does a git-source install ever pick up changes

**Answered by not depending on it.** The plugin now asks its own source repository for the
declared version and tells the user (README section 4; the approach is gstack's, and the
reasoning behind each guard is in the `vitals.py` comments rather than the README).
Whether `/plugin update` fetches anything while the version string stays put no longer
determines whether a stale install goes unnoticed - which was the actual risk.

Two consequences worth keeping in mind:

- **The version has to move on every release.** The check compares version strings, so a
  push without a bump is invisible to it. This does not conflict with D5, which was about
  the opposite failure: omitting `version` makes the commit SHA the version and every push
  looks like an update. Explicit version plus an owned check means releases are deliberate.
- **Still untested end to end**, because nothing is published yet. The check currently
  reports `UNKNOWN` and stays silent, which is the designed behavior for an unreachable
  source. Confirm against a real repository once it exists.

The original finding, kept for context: `installed_plugins.json` pointed at a frozen copy
under `~/.claude/plugins/cache/vickyhoo/session-vitals/1.0.0/`, three days stale and
missing every fix. It was not the code running - the directory-source marketplace serves
the working tree live, proved by `state.json` recording session ids that only newer code
writes. `doctor` now prints which file runs and flags a differing registered copy.

---

## Releasing

Distribution is decided by one string. `claude plugin update` compares the `version` in
`.claude-plugin/plugin.json` on the default branch and nothing else - the docs are
explicit: "the plugin is pinned to this string and users only receive updates when it
changes." Demonstrated on this machine: with the version left at 1.0.0, `update` answered
"already at the latest version" for a copy seven commits behind.

So the release checklist is short, and every item exists because something failed:

1. **Bump the version.** In `plugin.json`, `marketplace.json`, and `vitals.py`. A test
   asserts all three agree; hand-syncing three copies is a matter of time.
2. **`claude plugin validate .`** It parses every command's frontmatter. A malformed
   block is dropped whole - `description` included - and the command still loads,
   anonymous. `argument-hint: [set|get|unset]` did that: `|` opens a YAML block scalar.
   A test now guards the same shape, but validate sees things the test does not.
3. **Push to `main`.** The marketplace source is this repo's default branch and the
   plugin source is `"./"`, so main *is* the release line.
4. **`claude plugin tag --push`** to mark it. The tag also cross-checks that
   `plugin.json` and the marketplace entry agree before it will run.

A `dev` branch is optional and does not address the failure that actually occurred, which
was forgetting to bump rather than releasing too eagerly. If main should be free to move
without shipping, the structural change is to give the plugin entry an explicit `github`
source with `ref` or `sha` pointing at a tag, instead of `"./"`; then main and the release
line separate properly. Not worth it for a single-maintainer repo.

---

## Verification experiment (not scheduled)

Correlate compaction count against actual output quality, to establish whether the
metric predicts anything beyond "this session is long". A single machine does not have
the sample size to produce a convincing correlation, which is why the current
documentation makes no causal claim at all.
