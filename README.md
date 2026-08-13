# session-vitals

Keep an eye on long Claude Code sessions: how often they have been compacted, and a
checkpoint that survives compaction and reaches the next session.

```
🔴         22    137.9MB  acme--platform-rewrite
      \ 4 duplicate record(s) from resume/fork, not counted
🔴          9     67.4MB  acme--reporting-service
🟡          5     82.9MB  side-projects
```

## Requirements

**Python 3.8+.** It is the only dependency, but it is not free:

| Platform | Situation |
|---|---|
| macOS | `/usr/bin/python3` is present; without Xcode Command Line Tools the first run pops an install prompt |
| Linux | Preinstalled on nearly every distribution, usually nothing to do |
| Windows | **Not preinstalled**, you need to install Python first |

As of 2026 Claude Code ships as a native binary and carries no runtime of its own, so
you cannot assume Node or Python is already on the machine. Run `python3 --version`
before installing.

Desktop notifications need macOS `osascript`. Elsewhere they are skipped and everything
else works unchanged. `/session-vitals:doctor` tells you exactly how far your platform is
supported.

## Install

Paste this and let Claude do all of it:

> Install session-vitals: run `claude plugin marketplace add vickyhoo/session-vitals`
> then `claude plugin install session-vitals@vickyhoo`. Confirm `python3 --version`
> works, since that is the only dependency. Then tell me to restart this session, because
> hooks are captured at session start and this one will not have them.

Or type it yourself. In Claude Code:

```
/plugin marketplace add vickyhoo/session-vitals
/plugin install session-vitals@vickyhoo
```

Or in a terminal, which is the same thing without the prompt:

```bash
claude plugin marketplace add vickyhoo/session-vitals
claude plugin install session-vitals@vickyhoo
```

**Then restart the session.** Hook configuration is captured when a session starts, so
the one you installed from runs none of it - no reporting, no checkpointing, and no
error either. This is the single most common way to conclude the plugin does nothing.

Once restarted:

```
/session-vitals:doctor
```

It says explicitly whether the hooks are live in the session you are in.

## What it does

### 1. Compaction reporting

When the context window fills up, Claude Code compacts history: thousands of lines of
conversation become roughly ten thousand characters of summary. That is lossy.
`session-vitals` reports how many times it happened, how tightly, and how much
conversation has been summarized away.

| Compactions | Behavior |
|---|---|
| 0-2 | Completely silent |
| 3-5 | Suggests a checkpoint |
| 6+ | Suggests starting a fresh session |

Two signals each escalate one level: a gap under 512KB between compactions, and no
compaction markers found at all in a large transcript, which means Claude Code's format
may have changed and the numbers cannot be trusted.

Two more are reported without escalating: more than 4MB piled up since the last
compaction, which only means the session is busy, and lines that failed to parse, which
are usually records caught mid-flush.

**Be clear about what this metric supports:**

Known to be true: compaction happens, and each pass condenses a lot of conversation
into a small amount of text. It is genuinely lossy.

**Not claimed:** that "N compactions make the model hallucinate." That causal link is
unproven. Measurements show summary length does not shrink monotonically with
compaction count, and the same summary gets re-written into the transcript on resume
or fork (in one real session, 4 of 26 records were such duplicates). This tool hashes
summary content to deduplicate and measures gaps in bytes rather than line numbers,
so the number stays as close to reality as it can.

**It is a descriptive metric that helps you decide when to checkpoint. Not a diagnosis.**

### 2. Checkpointing around compaction

Right after compaction the model holds a fresh summary of everything that was just
condensed. That is when `session-vitals` asks it to write its current understanding into
the project's `PROGRESS.md`: what it is working on, why this approach, what is settled,
what comes next.

The request is injected as context, and context alone does not grant a turn - after
`/compact` the session simply idles. So the checkpoint is written when you next say
something, alongside whatever you asked for, not during the compaction itself.

It writes conclusions, not a log. Mechanical extraction only produces a transcript
dump; a summary worth reading has to come from the model that was actually there. The
file format follows the [Cline Memory Bank](https://docs.cline.bot/prompting/cline-memory-bank)
conventions.

**Nothing is created for you.** `enabled` is one global switch, so if compaction could
create the file, flipping it once would drop a `PROGRESS.md` into every repository a
compaction happened to occur in - including one you entered for ten minutes to read a log.

**The file's existence is the per-project opt-in.** Create it once with
`/session-vitals:checkpoint`, and from then on compaction keeps it current. Projects
without one stay untouched no matter how often they compact, which is the right default
for throwaway sessions.

**And the whole feature is off by default**, because it writes into your project directory
and that file will probably end up committed. Once enabled there are five safeguards: a credential
scan before every write (a hit aborts the whole write), a total size limit, per-session
blocks so concurrent sessions never clobber each other, an exclusive file lock, and a
refusal to write into a home directory.

Turn it on with:

```bash
/session-vitals:config set progress_md.enabled true
```

`/session-vitals:config` lists every setting, what it does, and which ones you have
changed. Prefer it over editing `~/.session-vitals/config.json` by hand: it checks the
key against the known settings and coerces the value to the declared type, where a
hand-written typo takes effect never and says nothing. `doctor` flags stray keys already
in the file for the same reason.

#### Reading it back

A checkpoint nothing ever reads is half a feature, so the SessionStart hook hands the
file to each new session. Small files are inlined; anything past `inject_max_bytes`
(8KB by default) is announced with its path and left for the model to open, because a
120KB dump would cost more context than it saves.

It is injected as **background, not instructions** - and the model is told that where
the file disagrees with the code, the code wins. A checkpoint is a snapshot of what was
believed at the time, and stale conclusions asserted with confidence are worse than none.

At `startup` the transcript is empty, so the launch directory is the only signal
available - the one moment it is worth trusting, since nothing has moved yet. On
`resume` the usual resolution applies. Both are tried, best evidence first.

```bash
/session-vitals:config set progress_md.inject false
```

To keep writing checkpoints by hand but never have compaction update them:

```bash
/session-vitals:config set progress_md.auto_update false
```

**You do not need to mention it in `CLAUDE.md`.** That works, but it costs context in
every session whether or not the file is relevant, has to be repeated per project, and
outlives the plugin if you uninstall it. The one case for adding it: other tools reading
`CLAUDE.md`/`AGENTS.md` cannot see this hook, so a one-line pointer there is worth it if
you also use Cursor, Codex or similar on the same repository.

#### Which directory does it write to

Not the shell's working directory. That value follows whatever the last command did -
one real session reported four different directories - and people routinely start
Claude Code from `~` while working on a project somewhere else.

Ask for one at any time with `/session-vitals:checkpoint`. It replaces this session's
block rather than appending, so running it whenever a decision settles is the intended
use, not just at the end. `/session-vitals:retire` is the same write plus the handoff
steps for starting a fresh session.

Writes go through a command rather than the model's Write tool, so the safeguards
actually apply:

```bash
python3 vitals.py write-progress --dir /path/to/project --session <id> <<'EOF'
what you are working on, why, what is settled, what comes next
EOF
```

- `--dir` given: trusted as-is, since not every project is a git repository. A home
  directory is still refused.
- `--dir` omitted: the target is derived from **every** directory the session recorded,
  not from the current one. Two kinds of noise are dropped first - paths inside
  dependency trees like `node_modules`, and any directory that is a strict ancestor of
  another recorded one, which is what the launch directory looks like. The common
  ancestor of what remains is the answer; if it sits inside a git repository, its root
  wins.

Dropping ancestors deliberately keeps *siblings*. That is what makes the awkward case
work: a workspace holding a frontend and a backend repository side by side. The shell
moves between them all session, so a single reading would flip depending on when
compaction fired, and progress spanning both would be filed under whichever one happened
to be current. Neither is an ancestor of the other, so both survive, and their common
ancestor is the workspace - where a checkpoint covering both belongs.

If the ancestor is your home directory, a top-level system path, or the session wandered
across unrelated trees, it refuses and says so instead of guessing.

The hook resolves the directory itself and puts the concrete path into the prompt, rather
than asking the model to name one it could only guess at. The script path is spelled out
in full for the same reason: `$CLAUDE_PLUGIN_ROOT` is exported to hook processes only, so
a prompt carrying that variable expands to nothing when the model runs it through Bash.

### 3. Update checking

The plugin asks its own source repository whether a newer version exists, rather than
waiting for the plugin manager to notice. It has to: the install path is
version-namespaced and `plugin.json` pins the version by hand, so nothing guarantees a
push is ever fetched. A stale copy that silently keeps running is the same class of
failure as a silent hook.

Most of the design exists because the obvious version compare misfires:

| Detail | Why |
|---|---|
| Version read through a commit-pinned raw URL, resolved with `git ls-remote` | GitHub's branch raw CDN can serve stale content for minutes after a push, so a check run right after a release reports "up to date" and the release goes unnoticed |
| Only a **strictly higher** remote counts | A stale CDN, or a local checkout ahead of the branch, otherwise produces a backwards "upgrade available" |
| Response must match a version shape | An error page must not become "upgrade to `<!DOCTYPE html>`" |
| Cache TTL of 1h when current, 12h when an update waits | Catch a release quickly; do not nag every session once you know |
| Snooze escalates 24h, 48h, 1 week, keyed by version | A declined update should not become a daily tax, but a *new* release has not been declined |
| Every failure is silence | A version check must never be why a session feels slow or broken |

Hooks only ever read the cache; the network request happens in a detached background
process, so session start never waits on it.

```bash
python3 vitals.py update-check            # UP_TO_DATE / UPGRADE_AVAILABLE / UNKNOWN
python3 vitals.py update-check --force    # ignore cache and snooze
python3 vitals.py update-check --snooze   # remind me later
```

Turn it off, and the plugin makes no network requests whatsoever:

```bash
/session-vitals:config set update_check false
```

## Commands

| Command | Purpose |
|---|---|
| `/session-vitals:config` | Show or change settings, with the valid keys and what each does |
| `/session-vitals:status` | Report on **this** session: what has been summarized away, whether it has a checkpoint, what to do next |
| `/session-vitals:scan` | Scan every session, ranked by compaction count |
| `/session-vitals:doctor` | Self check: runtime, which copy is running, version, heartbeats, whether hooks are live in this session, transcript format |
| `/session-vitals:checkpoint` | Save what this session concluded into `PROGRESS.md`, without ending it |
| `/session-vitals:retire` | Print a ready-to-run checkpoint command and the handoff steps |
| `vitals.py write-progress` | Write this session's progress block (what the post-compaction prompt asks for) |
| `vitals.py update-check` | Compare the installed version against the source repository |

### Why doctor exists

**Hook failures are silent.** This project started when its author found one of his own
hooks had been reading a field name that does not exist, quietly doing nothing for four
months, with no indication whatsoever.

So every successful run records a heartbeat, and `doctor` checks whether that heartbeat
is still beating, plus whether Claude Code's transcript field is still there. Run it
after installing, and again every once in a while.

**Sessions older than the install are wired to nothing.** Claude Code captures hook
configuration when a session starts, so a session already running when you installed
this plugin will never trigger it - no reporting, no checkpointing, no error either.
That is the worst case, because the long-running sessions most in need of a checkpoint
are exactly the ones old enough to miss it. `doctor` records the session id with every
heartbeat and tells you when the current session is one of them. **Restart the session
to pick the plugin up.**

## What it will not do

- **Touch transcript files.** Read only. `retire` only assembles and advises; it never
  moves or deletes anything, because the running process is still writing that file.
  Clean up disk space manually once a session is actually closed.
- **Send anything anywhere.** Nothing about your sessions, projects or conversations
  leaves the machine. The one exception is the update check above, which is an outbound
  request to GitHub carrying no payload; turn it off and there is no network traffic at
  all.
- **Store conversation content.** Only compaction counts, byte offsets and heartbeat
  timestamps.
- **Gate dangerous commands.** It used to, with a regex list and a confirmation dialog.
  That was removed: Claude Code's native rules do the same job better.

### Gating commands, without this plugin

`bypassPermissions` is not all-or-nothing - explicit `ask` rules still prompt in that
mode, and so does a built-in circuit breaker for `rm -rf /` and `rm -rf ~`. So write the
rules natively:

```json
{
  "permissions": {
    "ask": ["Bash(* sudo *)", "Bash(rm -rf *)", "Bash(* terraform destroy *)"],
    "defaultMode": "bypassPermissions"
  }
}
```

Native rules understand shell operators (`&&`, `|`, `;` each match independently), strip
wrappers like `timeout` and `xargs`, and wait for you instead of timing out into a denial
mid-command. A regex over the raw string, which is what this plugin had, does none of
that. Keeping a weaker duplicate of a built-in safety mechanism is worse than not
shipping one: it invites you to relax because something is watching.

## Adapting other AI tools

Underneath the hooks it is just subcommands, so anything can call it:

```bash
python3 vitals.py hook precompact   # JSON on stdin, needs transcript_path
python3 vitals.py status            # one session; --transcript to pick which
python3 vitals.py scan
python3 vitals.py doctor
```

The `hook` subcommand reads one JSON object from stdin and understands these fields:
`transcript_path`, `session_id`, `cwd`, `source`. Output is Claude Code hook JSON. Any
tool that can produce that shape can plug in.

## Development

```bash
python3 -m unittest discover -s tests -v
```

77 tests, nothing to install. Every fixture is synthetic. Real transcripts contain full
conversations and project paths, so they never enter the repository.

## License

MIT
