# Design: session-vitals

Decision record from the 2026-07-27 planning reviews (CEO review, engineering review,
and an outside voice via Codex CLI). Kept in the repo so the reasoning survives, not
just the code.

Originally named `claude-island`. Renamed when the direction moved from "a macOS notch
app" to "compaction reporting, checkpointing, and a dangerous-command gate". The naming
is medical throughout: vitals, `doctor`, `retire`.

---

## What was rejected, and why

The original proposal was a macOS notch app for Claude Code: live status, approvals in
the notch, plus session health. A landscape check killed two of its three
differentiators.

| Assumption | Reality |
|---|---|
| "Claude Code only is a differentiator" | Claude Island already occupies exactly that position (free, open source, single tool) |
| "Notch + AI agent is unexplored" | At least six competitors: Vibe Island (25 tools, paid), CodeIsland (13, MIT), Open Island (9), Claude Island (1), AgentNotch (2), Notchi (1) |

But the same competitor page confirmed one real gap, quoted verbatim:

> **None of the competitors offer** session health monitoring, context compaction
> tracking, or session degradation warnings.

All six report "what is the agent doing right now". None report "is this session still
worth continuing".

Platform check: Claude Code 2.1.218 fixed a quadratic slowdown in long sessions. That is
a **performance** fix, not summary quality. Multi-pass summarization loss is
architectural, and there is no evidence the platform is addressing it.

**Core judgment: the value is in the diagnosis, not the display.** A notch panel only
relocates a conclusion that has already been computed. The health engine was the one
piece with no existing implementation, and it was also the one piece already built. The
remaining ~1500 lines of SwiftUI would have rebuilt what six competitors already ship.

| Option | Effort / risk | Decision |
|---|---|---|
| A: build the full app | XL / high | rejected |
| B: fork CodeIsland, port the health engine | M / medium | rejected |
| **C: stay in Python, ship a Claude Code plugin** | **S / low** | **accepted** |
| D: submit a PR to CodeIsland | M / medium | rejected |

---

## The metric, and its limits

An earlier draft of this document claimed that "26 layers of recursive distillation
cause hallucination". **That claim is withdrawn.** Measurements:

```
26 compact summary records, 8.7K-17.2K characters each
Identical character counts: #6=#9=11704 · #11=#14=#16=13693 · #12=#17=10831
```

Three summaries matching to the character are not a coincidence. They are the same
summary re-written into the transcript on resume or fork, not new compaction. At least
4 of 26 records are duplicates. **And summary length shows no downward trend** (first
10.6K, last 12.8K, peak 17.2K), which contradicts the recursive-shrinking model.

What survives: compaction happened N times, condensing X lines of conversation into Y
characters, and that is lossy. The count is a **proxy**, not a diagnosed cause. No
user-facing material may claim causation.

Implementation consequences: hash summary content to deduplicate, and measure gaps in
**bytes** rather than line numbers. One JSONL line can be a huge tool result, so line
counts measure serialization behavior, not content volume.

## Measured performance

```
python3 cold start:       26ms
full scan of 427MB:       0.39s
```

A byte prefilter drops 99.99% of lines before any JSON parsing. **There is nothing to
optimize.** Earlier estimates of 50ms startup and 1-2s per scan were both wrong by an
order of magnitude, and the caching design they justified was dropped.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D2 | Single-file `vitals.py` with argparse subcommands | Claude Code executes hook scripts directly, so cross-file imports do not resolve. One file eliminates an entire class of silent failure and satisfies DRY by construction |
| D3 | Detect platform; disable approval off macOS and say so in `doctor` | `osascript` is macOS only. Fail-closed on a platform with no dialog implementation becomes denial of service |
| D4 | Hand-written minimal fixtures, never real transcripts | Real transcripts hold full conversations, project paths and possibly credentials. The counting logic only needs fields and offsets |
| D5 | Full distribution this cycle | Without an explicit `version`, the git commit SHA becomes the version and every push ships an "update" to users |
| D6 | Grade as base level plus modifiers | The original implementation entangled layer count with the rapid-compaction signal; adding a threshold meant re-deriving the whole decision tree |
| D7 | Descriptive metric, no causal claim | See the measurements above |
| D8 | Let the current model write the progress note | Mechanical extraction only yields a transcript dump. Calling Claude recursively from a hook adds reentrancy, latency and cost. `additionalContext` reaches the model that already holds the context |
| D9 | Full persistence contract for PROGRESS.md | Opt-in, credential scan before write, size limit, gitignore guidance, per-session blocks |
| D10 | `retire` never touches transcript files | Archiving a file the running process still writes can break the session, and it contradicted the stated reason E4 was deferred |
| D11 | Declare the Python 3.8+ dependency rather than remove it | See below |

Implementation note on D8: the write was moved from PreCompact to **PostCompact**.
PreCompact returns and compaction happens immediately, leaving the model no turn in
which to call a tool. After compaction it holds a fresh summary and a full turn.

## Runtime dependency

The distribution check initially covered only marketplace and versioning, and **missed
the runtime dependency entirely**. A user question surfaced it.

Key fact: **Claude Code now ships as a native binary; the npm distribution is officially
deprecated, and the native install carries no runtime.** So "rewrite it in Node for zero
dependencies" does not hold - users no longer necessarily have Node. Pure shell is not a
way out either: hooks pass JSON, shell parsing needs `jq`, and `jq` is rarer than Python.

| Platform | python3 | Risk |
|---|---|---|
| macOS | `/usr/bin/python3` present; first run without Xcode CLT prompts an install | soft |
| Linux | preinstalled nearly everywhere | negligible |
| Windows | not preinstalled | hard |

Decision: declare the dependency instead of eliminating it. README states Python 3.8+;
`doctor` checks it first with per-platform guidance; hooks exit quietly when python3 is
missing rather than spamming errors. Windows users install Python themselves, and that
attrition is accepted. The escalation path stays open: if real Windows feedback arrives,
build multi-platform binaries in Go or Rust (human ~1 week / CC ~4h). No build pipeline
for a hypothetical audience.

---

## Outside voice (Codex CLI 0.144.1)

Eight findings, all addressed:

1. Core causal claim unproven -> D7
2. "150 lines" is a meaningless distance metric -> switched to bytes
3. PreCompact off-by-one: the pending compaction is not in the transcript yet, so the
   3-5 band could never fire for it -> count it explicitly
4. No semantic extraction mechanism -> D8
5. No safe persistence contract -> D9
6. Regex approval is trivially bypassable via variables, aliases, `eval`, `sh -c` -> the
   README states plainly that it is a speed bump, not a security boundary
7. `retire` unsafe and contradicts E4's deferral rationale -> D10
8. Documentation inconsistent with the single-file decision -> this document

## What already existed

- `session-health.py` (215 lines) and `worklog.py` (155 lines), both verified working,
  became the raw material for `vitals.py` rather than being rewritten
- The Swift bridge's danger-pattern table and fallback logic were ported into Python;
  the Swift tree was then deleted

## Not in scope

- E1 notch integration, E4 trends and archival - see TODOS.md
- Dialog implementations for Linux and Windows: approval disables itself instead,
  because those implementations could not be verified on available hardware
- A causal verification experiment: a single machine lacks the sample size

## Measured baseline

- 54 sessions, 427 MB total (609 MB across all of `.claude/projects`)
- Worst session: 26 records / 22 after dedup, 39,476 lines, 137.9 MB
- 2 sessions recommend a fresh start, 1 recommends a checkpoint, ~205 MB reclaimable
- Implementation trap worth remembering: compaction detection must `json.loads` and
  verify `isCompactSummary is True`. Plain string matching counts a conversation that
  merely discusses the field name, which produced 5 phantom compactions on the very
  session where this was being written.
- Encoding trap: passing text to AppleScript through environment variables mangles
  non-ASCII (`system attribute` reads it as Mac Roman). Use argv, which is also
  injection-safe because argv is data, not code.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR | 5 proposals, 3 accepted, 2 deferred |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 9 issues, 0 critical gaps |
| Outside Voice | codex exec | Independent 2nd opinion | 1 | issues_found | 8 findings, all addressed |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | no UI scope |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **CODEX:** all 8 findings addressed: 3 became P1 tasks (dedup and byte metric,
  off-by-one, persistence contract), 3 were absorbed into decisions (D7/D8/D10), 2
  became documentation requirements (bypass disclaimer, architecture sync).
- **CROSS-MODEL:** no opposing positions. The outside voice overturned a premise this
  review had accepted; it was verified against measurements and adopted, so nothing
  required user arbitration.
- **VERDICT:** CEO + ENG CLEARED - implemented and shipped.

NO UNRESOLVED DECISIONS
