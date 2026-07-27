#!/usr/bin/env python3
"""
session-vitals — watch long Claude Code sessions and leave a record before they degrade.

Three jobs:
  1. Report how often the session transcript has been compacted, how tightly, and
     how much conversation got summarized away
  2. Persist working context before compaction swallows it
  3. Gate dangerous Bash commands behind a confirmation dialog

What this metric does and does not claim (do not cross this line in docs or marketing):
compaction count is a DESCRIPTIVE metric, not a proven cause. What is known to be true:
compaction happens, and each pass condenses thousands of lines of conversation into
roughly ten thousand characters (genuinely lossy). What is NOT claimed, because it is
unproven: that "N compactions cause the model to hallucinate". Measurements show summary
length does not shrink monotonically with compaction count, and the same summary gets
re-written into the transcript on resume or fork, so a naive count overstates things.
This implementation hashes summary content to deduplicate, and measures gaps in bytes
rather than line numbers.

Runtime requirement: Python 3.8+. The approval dialog needs macOS `osascript`;
on other platforms approval disables itself automatically.
"""

import argparse
import fcntl
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0.0"

# ── Grading thresholds ──────────────────────────────────────────────────────
WARN_LAYERS = 3                    # suggest checkpointing at this many
CRIT_LAYERS = 6                    # suggest starting a fresh session
RAPID_GAP_BYTES = 512 * 1024       # gap smaller than this = compacting too tightly
TAIL_HOT_BYTES = 4 * 1024 * 1024   # this much piled up since last compaction

STATE_DIR = Path.home() / ".session-vitals"
CONFIG_PATH = STATE_DIR / "config.json"
STATE_PATH = STATE_DIR / "state.json"

MARKER = b"isCompactSummary"
IS_MACOS = sys.platform == "darwin"

DEFAULT_DANGER_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)",
    r"\brm\s+--(recursive|force)\b",
    r"\bgit\s+push\b.*--force",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[a-zA-Z]*f",
    r"\bdd\s+if=",
    r"\bmkfs\b",
    r"\bsudo\b",
    r">\s*/dev/(disk|sd)",
    r"\bDROP\s+(TABLE|DATABASE)\b",
    r"\bTRUNCATE\s+TABLE\b",
    r"\bkubectl\s+delete\b",
    r"\bterraform\s+destroy\b",
]

# Credential scan before writing into a project file. A hit aborts the whole write:
# better to skip one checkpoint than to commit a secret into git history.
SECRET_PATTERNS = [
    (r"sk-ant-[A-Za-z0-9_\-]{20,}", "Anthropic API key"),
    (r"\bgh[pousr]_[A-Za-z0-9]{30,}", "GitHub token"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"\bxox[baprs]-[A-Za-z0-9\-]{10,}", "Slack token"),
    (r"\bAIza[0-9A-Za-z_\-]{35}\b", "Google API key"),
    (r"(?i)\b(api[_-]?key|secret|passwd|password|access[_-]?token)\b\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{16,}", "credential assignment"),
]


# ── Config and state ────────────────────────────────────────────────────────

def load_config():
    cfg = {
        "approval": "default",          # off | default | all
        "approval_timeout_seconds": 60,
        "danger_patterns": DEFAULT_DANGER_PATTERNS,
        "progress_md": {"enabled": False, "max_bytes": 120_000, "filename": "PROGRESS.md"},
    }
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            user = json.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except (OSError, ValueError):
        pass
    return cfg


def _read_state():
    try:
        with STATE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def beat(event):
    """Record a successful run. Hook failures are silent; only a heartbeat proves life."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state = _read_state()
        state.setdefault("heartbeat", {})[event] = _now()
        state["version"] = VERSION
        tmp = STATE_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        tmp.replace(STATE_PATH)
    except OSError:
        pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Transcript scanning ─────────────────────────────────────────────────────

def scan(path):
    """
    Stream the transcript. Never read it whole: real transcripts reach 138MB and up.

    Returns None if unreadable. Otherwise a dict:
      compacts       deduplicated records [{line, offset, size, digest}]
      duplicates     records judged to be replays/forks
      total_bytes    file size
      parse_failures lines that failed to parse (truncated by a concurrent write)
      has_marker     whether the literal isCompactSummary ever appeared
    """
    p = Path(path) if path else None
    if not p or not p.is_file():
        return None

    compacts, seen = [], set()
    duplicates = parse_failures = 0
    offset = total_lines = 0
    has_marker = False

    try:
        with p.open("rb") as f:
            for total_lines, raw in enumerate(f, 1):
                start = offset
                offset += len(raw)
                # The byte prefilter is only for speed. A hit must still be parsed and
                # field-checked, or a conversation that merely discusses this field name
                # gets counted as real compaction events.
                if MARKER not in raw:
                    continue
                has_marker = True
                try:
                    d = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    parse_failures += 1
                    continue
                if d.get("isCompactSummary") is not True:
                    continue

                text = _summary_text(d)
                digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]
                if digest in seen:
                    duplicates += 1      # replay on resume, or a fork; not new compaction
                    continue
                seen.add(digest)
                compacts.append({
                    "line": total_lines,
                    "offset": start,
                    "size": len(text),
                    "digest": digest,
                })
    except OSError:
        return None

    return {
        "path": str(p),
        "compacts": compacts,
        "duplicates": duplicates,
        "total_bytes": offset,
        "total_lines": total_lines,
        "parse_failures": parse_failures,
        "has_marker": has_marker,
    }


def _summary_text(d):
    msg = d.get("message") or {}
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(b.get("text", "") for b in c if isinstance(b, dict))
    return json.dumps(d.get("summary", ""), ensure_ascii=False)


def metrics(sc, pending=0):
    """Turn a scan into grading inputs. `pending` counts compactions about to happen."""
    compacts = sc["compacts"]
    layers = len(compacts) + pending
    gaps = [b["offset"] - a["offset"] for a, b in zip(compacts, compacts[1:])]
    tail = sc["total_bytes"] - compacts[-1]["offset"] if compacts else sc["total_bytes"]
    summarized = sum(c["size"] for c in compacts)

    return {
        "layers": layers,
        "recorded": len(compacts),
        "pending": pending,
        "duplicates": sc["duplicates"],
        "rapid": sum(1 for g in gaps if g < RAPID_GAP_BYTES),
        "min_gap": min(gaps) if gaps else None,
        "tail_bytes": tail,
        "total_bytes": sc["total_bytes"],
        "total_lines": sc["total_lines"],
        "summarized_chars": summarized,
        "parse_failures": sc["parse_failures"],
        # A large file with zero markers means the transcript format may have changed.
        # Keep the threshold high: a long session that simply has not compacted yet
        # legitimately has no markers, and crying wolf about that trains people to
        # ignore the warning.
        "format_suspect": (not sc["has_marker"]) and sc["total_bytes"] > 20 * 1024 * 1024,
    }


# ── Grading: base level plus modifiers ──────────────────────────────────────

LEVELS = ["ok", "warn", "crit"]


def grade(m):
    """
    Two steps. Compaction count alone sets the base level; independent signals then
    escalate it. Adding a signal means adding one modifier, not re-deriving a decision tree.
    """
    if m["layers"] >= CRIT_LAYERS:
        level, base = "crit", "compacted %d times" % m["layers"]
    elif m["layers"] >= WARN_LAYERS:
        level, base = "warn", "compacted %d times" % m["layers"]
    else:
        level, base = "ok", "compacted %d times" % m["layers"]

    modifiers = []
    if m["rapid"]:
        modifiers.append("%d gap(s) under %dKB" % (m["rapid"], RAPID_GAP_BYTES // 1024))
    if m["tail_bytes"] > TAIL_HOT_BYTES:
        modifiers.append("%.1fMB piled up since last compaction" % (m["tail_bytes"] / 1048576))
    if m["format_suspect"]:
        modifiers.append("no compaction markers found, transcript format may have changed")
    if m["parse_failures"]:
        modifiers.append("%d line(s) failed to parse" % m["parse_failures"])

    # Each qualifying signal bumps one level, capped at crit.
    bump = sum(1 for k in ("rapid", "format_suspect") if m.get(k))
    if bump:
        level = LEVELS[min(LEVELS.index(level) + 1, len(LEVELS) - 1)]

    return level, base, modifiers


def advice(level):
    if level == "crit":
        return "checkpoint and start a fresh session: /session-vitals:retire"
    if level == "warn":
        return "checkpoint your progress: /session-vitals:retire"
    return ""


# ── Platform capability ─────────────────────────────────────────────────────

def approval_available():
    """Approval needs a GUI dialog. Without one, disable it loudly rather than fail silently."""
    return IS_MACOS and _which("osascript") is not None


def _which(name):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = Path(d) / name
        if p.is_file() and os.access(str(p), os.X_OK):
            return str(p)
    return None


# ── Notifications ───────────────────────────────────────────────────────────

NOTIFY_SCRIPT = """on run argv
  display notification (item 1 of argv) with title (item 2 of argv) sound name "Tink"
end run"""


def notify(title, message):
    """
    Pass all text to AppleScript through argv.

    Two reasons. Encoding: UTF-8 read back via `system attribute` gets interpreted as
    Mac Roman, which mangles any non-ASCII text. Safety: argv is data, not code, so
    command text can never be executed as script.
    """
    if _which("terminal-notifier"):
        try:
            subprocess.run(["terminal-notifier", "-title", title, "-message", message],
                           check=True, capture_output=True, timeout=5)
            return True
        except (OSError, subprocess.SubprocessError):
            pass
    if not IS_MACOS or not _which("osascript"):
        return False
    return _osascript(NOTIFY_SCRIPT, [message, title], timeout=8) is not None


def _osascript(script, args, timeout):
    """Script body goes in as UTF-8 on stdin; every variable value goes in as argv."""
    try:
        r = subprocess.run(["osascript", "-"] + [str(a) for a in args],
                           input=script.encode("utf-8"),
                           capture_output=True, timeout=timeout)
        return r.stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.SubprocessError):
        return None


# ── Approval ────────────────────────────────────────────────────────────────

def is_dangerous(cmd, cfg):
    mode = cfg.get("approval", "default")
    if mode == "off":
        return False
    if mode == "all":
        return True
    return any(re.search(p, cmd) for p in cfg.get("danger_patterns", DEFAULT_DANGER_PATTERNS))


def ask_approval(cmd, cfg):
    """
    Return allow or deny. Anything short of an explicit Allow is a deny.

    Why not defer: defer means "fall back to the normal permission flow", and under
    bypassPermissions that flow runs the command. For a dialog nobody may be watching,
    defer is a green light.
    """
    timeout = int(cfg.get("approval_timeout_seconds", 60))
    script = """on run argv
  set r to display dialog (item 1 of argv) ¬
    with title "session-vitals · dangerous command" ¬
    buttons {"Deny", "Allow"} default button "Deny" ¬
    with icon caution giving up after %d
  if gave up of r then return "TIMEOUT"
  return button returned of r
end run""" % timeout
    out = _osascript(script, [cmd[:800]], timeout=timeout + 10)
    return "allow" if out == "Allow" else "deny"


# ── PROGRESS.md ─────────────────────────────────────────────────────────────

def scan_secrets(text):
    return [label for pat, label in SECRET_PATTERNS if re.search(pat, text)]


def write_progress(cwd, session_id, body, cfg):
    """
    Write into a per-session block so concurrent sessions never clobber each other.
    Scan for credentials first; a hit aborts the write. Returns (ok, reason).
    """
    opts = cfg.get("progress_md", {})
    if not opts.get("enabled"):
        return False, "not enabled (set progress_md.enabled=true in %s)" % CONFIG_PATH

    leaked = scan_secrets(body)
    if leaked:
        return False, "possible credentials detected (%s), write aborted" % ", ".join(sorted(set(leaked)))

    max_bytes = int(opts.get("max_bytes", 120_000))
    target = Path(cwd) / opts.get("filename", "PROGRESS.md")
    sid = re.sub(r"[^A-Za-z0-9_\-]", "", session_id)[:32] or "unknown"
    begin, end = "<!-- session-vitals:%s -->" % sid, "<!-- /session-vitals:%s -->" % sid
    block = "%s\n_updated %s_\n\n%s\n%s\n" % (begin, _now(), body.rstrip(), end)

    try:
        with _locked(target) as f:
            old = f.read()
            if begin in old and end in old:
                head, rest = old.split(begin, 1)
                _, tail = rest.split(end, 1)
                new = head + block + tail
            else:
                header = "" if old.strip() else "# Project progress\n\n_Maintained by session-vitals, one block per session._\n\n"
                new = (old.rstrip() + "\n\n" if old.strip() else header) + block
            if len(new.encode("utf-8")) > max_bytes:
                return False, "exceeds the %d byte limit, write aborted" % max_bytes
            f.seek(0)
            f.truncate()
            f.write(new)
        return True, str(target)
    except OSError as e:
        return False, "write failed: %s" % e


class _locked:
    """Open with an exclusive lock so concurrent sessions cannot tear the file."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.f = open(str(self.path), "a+", encoding="utf-8")
        fcntl.flock(self.f.fileno(), fcntl.LOCK_EX)
        self.f.seek(0)
        return self.f

    def __exit__(self, *exc):
        fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
        self.f.close()
        return False


# ── Hook output ─────────────────────────────────────────────────────────────

def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))


def health_context(m, level, base, modifiers):
    """Context injected for Claude. States observable facts only, asserts no causation."""
    detail = base + ("; " + "; ".join(modifiers) if modifiers else "")
    return (
        "[session-vitals] Current session transcript: %s. "
        "%d lines of conversation have been condensed into roughly %d characters of summary.\n"
        "Suggested action: %s\n"
        "Mention this to the user in a sentence or two at the end of your reply. "
        "Do not interrupt what they are currently working on."
        % (detail, m["total_lines"], m["summarized_chars"], advice(level))
    )


# ── Hook handlers ───────────────────────────────────────────────────────────

def hook_health(payload, event, pending):
    sc = scan(payload.get("transcript_path"))
    beat(event)
    if not sc:
        return
    m = metrics(sc, pending=pending)
    level, base, modifiers = grade(m)
    if level == "ok":
        return  # healthy sessions stay completely silent

    icon = "🔴" if level == "crit" else "🟡"
    notify("session-vitals", "%s %s" % (base, "; ".join(modifiers) if modifiers else ""))
    ctx = health_context(m, level, base, modifiers)
    emit({
        "systemMessage": "%s session-vitals: %s | %s" % (icon, base, advice(level)),
        "hookSpecificOutput": {"hookEventName": event, "additionalContext": ctx},
        "additionalContext": ctx,
    })


def hook_precompact(payload):
    # The compaction about to happen is not in the transcript yet. Count it explicitly,
    # or the pre-compaction warning is always one behind.
    hook_health(payload, "PreCompact", pending=1)


def hook_sessionstart(payload):
    hook_health(payload, "SessionStart", pending=0)


def hook_postcompact(payload):
    """
    Compaction just finished: the model holds a fresh summary and still has a full turn
    for tool calls. This is the moment to have it write its own progress note. Mechanical
    extraction only produces a transcript dump; what actually matters (where things stand,
    why, what is next) only the model can articulate.
    """
    beat("PostCompact")
    cfg = load_config()
    if not cfg.get("progress_md", {}).get("enabled"):
        return
    fname = cfg["progress_md"].get("filename", "PROGRESS.md")
    ctx = (
        "[session-vitals] The context was just compacted. Before continuing your current "
        "task, write your current understanding of this project into %s: what you are "
        "working on, why this approach, decisions already settled, and what comes next. "
        "Use the existing session-vitals block format and update only the block belonging "
        "to this session. Write conclusions, not a log of the conversation." % fname
    )
    emit({"hookSpecificOutput": {"hookEventName": "PostCompact", "additionalContext": ctx}})


def hook_pretooluse(payload):
    beat("PreToolUse")
    cfg = load_config()
    tool = payload.get("tool_name") or ""
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if tool != "Bash" or not cmd or not is_dangerous(cmd, cfg):
        return

    if not approval_available():
        # No dialog available. Let the command through rather than block it: denying
        # everything on a platform where approval was never implemented turns the
        # plugin into a denial of service with no visible cause.
        emit({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "defer",
            },
            "systemMessage": "session-vitals: no approval dialog on this platform (needs macOS), check skipped",
        })
        return

    decision = ask_approval(cmd, cfg)
    out = {"hookEventName": "PreToolUse", "permissionDecision": decision}
    if decision == "deny":
        out["permissionDecisionReason"] = (
            "Blocked by session-vitals: the user did not approve this dangerous command "
            "(denied or timed out). Ask the user to confirm manually if it needs to run."
        )
        notify("session-vitals", "Blocked: %s" % cmd[:80])
    emit({"hookSpecificOutput": out})


# ── Subcommands ─────────────────────────────────────────────────────────────

def cmd_hook(args):
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        sys.exit(0)
    {
        "precompact": hook_precompact,
        "postcompact": hook_postcompact,
        "sessionstart": hook_sessionstart,
        "pretooluse": hook_pretooluse,
    }[args.event](payload)
    sys.exit(0)


def cmd_scan(args):
    root = Path.home() / ".claude" / "projects"
    files = [f for f in root.rglob("*.jsonl") if "subagents" not in f.parts]
    rows = []
    for f in files:
        sc = scan(f)
        if not sc or not sc["compacts"]:
            continue
        m = metrics(sc)
        level, base, mods = grade(m)
        rows.append((level, m, f, base, mods))

    rows.sort(key=lambda r: (-r[1]["layers"], -r[1]["total_bytes"]))
    if not rows:
        print("No sessions have been compacted yet.")
        return

    total_mb = sum(f.stat().st_size for f in files) / 1048576
    print("\nScanned %d sessions, %.0f MB total\n" % (len(files), total_mb))
    print("%-4s %8s %9s  %s" % ("", "compact", "size", "project"))
    print("-" * 74)
    icons = {"crit": "🔴", "warn": "🟡", "ok": "🟢"}
    for level, m, f, base, mods in rows[:25]:
        raw = f.parent.name
        name = raw.split("-Projects-", 1)[1] if "-Projects-" in raw else raw
        print("%-3s %8d %8.1fMB  %s" % (icons[level], m["layers"],
                                        m["total_bytes"] / 1048576, name[:44]))
        if m["duplicates"]:
            print("      \\ %d duplicate record(s) from resume/fork, not counted" % m["duplicates"])
    print("-" * 74)

    crit = [r for r in rows if r[0] == "crit"]
    warn = [r for r in rows if r[0] == "warn"]
    print("\n🔴 %d suggest a fresh session   🟡 %d suggest a checkpoint\n" % (len(crit), len(warn)))
    for level, m, f, base, mods in crit[:3]:
        print("  %s" % f.parent.name)
        print("    %s%s" % (base, "; " + "; ".join(mods) if mods else ""))
        print("    -> %s\n" % advice(level))


def cmd_doctor(args):
    cfg = load_config()
    state = _read_state()
    ok, bad, warn = "OK  ", "FAIL", "WARN"
    print("\nsession-vitals v%s - self check\n" % VERSION)

    # 1. Runtime
    v = sys.version_info
    good = (v.major, v.minor) >= (3, 8)
    print("[%s] Python %d.%d.%d %s" % (ok if good else bad, v.major, v.minor, v.micro,
                                       "" if good else "(needs 3.8+)"))

    # 2. Platform capability
    if approval_available():
        print("[%s] Approval dialog available (%s)" % (ok, platform.system()))
    else:
        print("[%s] Approval dialog unavailable (%s, needs macOS + osascript). "
              "Diagnostics and checkpointing are unaffected." % (warn, platform.system()))

    # 3. Heartbeat
    hb = state.get("heartbeat") or {}
    if not hb:
        print("[%s] No heartbeat ever recorded - hooks may not be wired up, "
              "or nothing has triggered yet" % bad)
    else:
        for ev in ("SessionStart", "PreCompact", "PostCompact", "PreToolUse"):
            ts = hb.get(ev)
            print("[%s] %-14s last run %s" % (ok if ts else warn, ev, ts or "never"))

    # 4. Transcript format still recognizable.
    # Checking only the newest file misleads: the current session often has not
    # compacted yet. Look at the largest few; any marker proves the field is alive.
    root = Path.home() / ".claude" / "projects"
    try:
        cands = [f for f in root.rglob("*.jsonl") if "subagents" not in f.parts]
    except OSError:
        cands = []
    if not cands:
        print("[%s] No transcripts found" % warn)
    else:
        biggest = sorted(cands, key=lambda f: f.stat().st_size, reverse=True)[:5]
        found = next((f for f in biggest if (scan(f) or {}).get("has_marker")), None)
        if found:
            print("[%s] Transcript format recognized (marker found in %s)" % (ok, found.name[:12]))
        else:
            print("[%s] No compaction markers in the %d largest transcripts - "
                  "the field may have changed and counts will be wrong" % (bad, len(biggest)))

    # 5. PROGRESS.md switch
    pm = cfg.get("progress_md", {})
    if pm.get("enabled"):
        print("[%s] PROGRESS.md writing enabled (limit %d bytes)" % (ok, pm.get("max_bytes", 0)))
    else:
        print("[%s] PROGRESS.md writing disabled. To enable, set progress_md.enabled=true "
              "in %s" % (warn, CONFIG_PATH))
    print()


def cmd_retire(args):
    """
    Only assembles the state and prints the handoff steps.

    Deliberately does not touch the transcript file: the running process is still
    writing to it, and archiving a live file can break the session. Disk cleanup
    is a manual step.
    """
    print("\nSession retirement checklist\n")
    print("1. Checkpoint: write current progress into the project's PROGRESS.md")
    print("   (PostCompact does this automatically when enabled; can also be done now)")
    print("2. Save context: /context-save")
    print("3. Start a fresh session, then /context-restore")
    print("\nWorking directory: %s" % os.getcwd())
    print("Note: this command never deletes or moves transcript files. "
          "To reclaim disk space, confirm the session is closed and do it manually.\n")


def main():
    ap = argparse.ArgumentParser(prog="vitals", description="session-vitals v%s" % VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("hook", help="invoked by Claude Code hooks")
    h.add_argument("event", choices=["precompact", "postcompact", "sessionstart", "pretooluse"])
    h.set_defaults(func=cmd_hook)

    sub.add_parser("scan", help="scan every session and rank them").set_defaults(func=cmd_scan)
    sub.add_parser("doctor", help="self check: runtime, wiring, heartbeat, format").set_defaults(func=cmd_doctor)
    sub.add_parser("retire", help="print the session retirement checklist").set_defaults(func=cmd_retire)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
