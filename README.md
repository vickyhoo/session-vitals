# session-vitals

Keep an eye on long Claude Code sessions: how often they have been compacted, a
checkpoint before compaction swallows your progress, and a gate in front of dangerous
Bash commands.

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

The approval dialog needs macOS `osascript`. **On other platforms approval turns itself
off**; diagnostics and checkpointing keep working. `/session-vitals:doctor` tells you
exactly how far your platform is supported.

## Install

```bash
/plugin marketplace add vickyhoo/session-vitals
/plugin install session-vitals@vickyhoo
/reload-plugins
```

Run the self check right away:

```
/session-vitals:doctor
```

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

Three signals each escalate one level: gaps under 512KB, more than 4MB piled up since
the last compaction, and no compaction markers found at all (which means Claude Code's
transcript format may have changed and the numbers cannot be trusted).

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

Right after compaction the model holds a fresh summary and still has a full turn
available for tool calls. That is when `session-vitals` asks it to write its current
understanding into the project's `PROGRESS.md`: what it is working on, why this
approach, what is settled, what comes next.

It writes conclusions, not a log. Mechanical extraction only produces a transcript
dump; a summary worth reading has to come from the model that was actually there. The
file format follows the [Cline Memory Bank](https://docs.cline.bot/prompting/cline-memory-bank)
conventions.

**This is off by default**, because it writes into your project directory and that file
will probably end up committed. Once enabled there are four safeguards: a credential
scan before every write (a hit aborts the whole write), a total size limit, per-session
blocks so concurrent sessions never clobber each other, and a refusal to write into a
home directory.

Enable it in `~/.session-vitals/config.json`:

```json
{
  "progress_md": { "enabled": true, "max_bytes": 120000, "filename": "PROGRESS.md" }
}
```

#### Which directory does it write to

Not the shell's working directory. That value follows whatever the last command did -
one real session reported four different directories - and people routinely start
Claude Code from `~` while working on a project somewhere else.

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
  not from the current one. Their common ancestor is stable across the session; if that
  ancestor sits inside a git repository, its root wins.

That handles the case a single reading gets wrong: a workspace holding a frontend and a
backend repository side by side. The shell moves between them all session, so the answer
would flip depending on when compaction fired, and progress spanning both would be filed
under whichever one happened to be current. The common ancestor is the workspace, which
is where a checkpoint covering both belongs.

If the ancestor is your home directory, a top-level system path, or the session wandered
across unrelated trees, it refuses and says so instead of guessing.

The prompt to do this is injected by the SessionStart hook when the session starts with
`source: compact` - that is the first moment after compaction where the model both holds
a fresh summary and can still call a tool. It is told to pass the real project directory,
precisely because the shell's directory cannot be trusted.

### 3. Dangerous command gate

Commands like `rm -rf`, `git push --force`, `git reset --hard`, `DROP TABLE` and
`terraform destroy` raise a confirmation dialog. **Anything short of an explicit Allow
is a deny**, including a timeout.

Because if you run with `bypassPermissions`, "no decision" means the command executes,
and for a dialog nobody may be watching, that is a green light.

> **This is a speed bump, not a security boundary.**
>
> Regex matching stops slips, not intent. Variables, aliases, shell functions, `eval`,
> `sh -c`, wrapping it in a script, a different database client - all trivially get
> around it. Do not relax because it is installed. That would leave you worse off than
> not having it.

Tune or disable:

```json
{
  "approval": "default",
  "approval_timeout_seconds": 60,
  "danger_patterns": ["custom regexes, replaces the default list"]
}
```

`approval` takes `off`, `default` (dangerous commands only) or `all` (every Bash call).

## Commands

| Command | Purpose |
|---|---|
| `/session-vitals:scan` | Scan every session, ranked by compaction count |
| `/session-vitals:doctor` | Self check: runtime, hook wiring, heartbeat, transcript format |
| `/session-vitals:retire` | Assemble current progress and print handoff steps |
| `vitals.py write-progress` | Write this session's progress block (what the post-compaction prompt asks for) |

### Why doctor exists

**Hook failures are silent.** This project started when its author found one of his own
hooks had been reading a field name that does not exist, quietly doing nothing for four
months, with no indication whatsoever.

So every successful run records a heartbeat, and `doctor` checks whether that heartbeat
is still beating, plus whether Claude Code's transcript field is still there. Run it
after installing, and again every once in a while.

## What it will not do

- **Touch transcript files.** Read only. `retire` only assembles and advises; it never
  moves or deletes anything, because the running process is still writing that file.
  Clean up disk space manually once a session is actually closed.
- **Send anything anywhere.** Entirely local, no network calls.
- **Store conversation content.** Only compaction counts, byte offsets and heartbeat
  timestamps.

## Adapting other AI tools

Underneath the hooks it is just subcommands, so anything can call it:

```bash
python3 vitals.py hook precompact   # JSON on stdin, needs transcript_path
python3 vitals.py scan
python3 vitals.py doctor
```

The `hook` subcommand reads one JSON object from stdin and understands these fields:
`transcript_path`, `session_id`, `cwd`, `tool_name`, `tool_input.command`. Output is
Claude Code hook JSON. Any tool that can produce that shape can plug in.

## Development

```bash
python3 -m unittest discover -s tests -v
```

36 tests, nothing to install. Every fixture is synthetic. Real transcripts contain full
conversations and project paths, so they never enter the repository.

## License

MIT
