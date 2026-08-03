"""
Tests for session-vitals. Standard library unittest, no pytest: the project declares
Python 3.8+ as its only dependency, and the test suite should not add a second one.

    python3 -m unittest discover -s tests -v

Every fixture is synthetic. Real transcripts contain full conversations, project paths
and possibly pasted credentials, so they never enter the repository. The counting logic
only cares about fields and offsets, which synthetic data covers fine.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vitals  # noqa: E402


# ── Fixture builders ────────────────────────────────────────────────────────

def compact(text):
    """A genuine compaction summary record."""
    return json.dumps({
        "type": "user",
        "isCompactSummary": True,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }, ensure_ascii=False)


def chat(text):
    """An ordinary conversation record."""
    return json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }, ensure_ascii=False)


def padding(n):
    """Bulk records, used to push byte offsets apart."""
    return [chat("x" * 2000) for _ in range(n)]


class Fx:
    """Write lines into a temporary jsonl file."""

    def __init__(self, lines):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl",
                                               delete=False, encoding="utf-8")
        for ln in lines:
            self.tmp.write(ln + "\n")
        self.tmp.close()
        self.path = self.tmp.name

    def __enter__(self):
        return self.path

    def __exit__(self, *exc):
        Path(self.path).unlink(missing_ok=True)
        return False


# ── Scanning and counting ───────────────────────────────────────────────────

class TestScan(unittest.TestCase):

    def test_counts_distinct_compacts(self):
        with Fx([chat("a"), compact("summary one"), chat("b"),
                 compact("summary two"), compact("summary three")]) as p:
            m = vitals.metrics(vitals.scan(p))
        self.assertEqual(m["layers"], 3)
        self.assertEqual(m["duplicates"], 0)

    def test_dedupes_replayed_summaries(self):
        """
        The same summary gets re-written into the transcript on resume or fork.
        Measured on a real session: 4 of 26 records were duplicates like this.
        Without dedup the session looks more degraded than it is.
        """
        with Fx([compact("same summary"), chat("x"),
                 compact("same summary"), compact("different")]) as p:
            m = vitals.metrics(vitals.scan(p))
        self.assertEqual(m["layers"], 2)
        self.assertEqual(m["duplicates"], 1)

    def test_ignores_self_reference(self):
        """
        A conversation discussing the isCompactSummary field must not be counted as
        real compaction. The first implementation used plain string matching and
        reported 5 compactions for a session that was merely talking about the field.
        """
        with Fx([chat("we need to check the isCompactSummary field"),
                 chat('the code reads d.get("isCompactSummary") is True'),
                 compact("this one is an actual summary")]) as p:
            sc = vitals.scan(p)
        self.assertTrue(sc["has_marker"])          # the literal did appear
        self.assertEqual(len(sc["compacts"]), 1)   # but only one real record

    def test_empty_file(self):
        with Fx([]) as p:
            m = vitals.metrics(vitals.scan(p))
        self.assertEqual(m["layers"], 0)
        self.assertEqual(vitals.grade(m)[0], "ok")

    def test_missing_file(self):
        self.assertIsNone(vitals.scan("/nonexistent/path.jsonl"))
        self.assertIsNone(vitals.scan(None))

    def test_truncated_line_counted_not_fatal(self):
        """Reading while Claude Code writes yields a half line. Skip it, but count it."""
        with Fx([compact("good summary"), '{"isCompactSummary": true, "message"']) as p:
            sc = vitals.scan(p)
        self.assertEqual(len(sc["compacts"]), 1)
        self.assertEqual(sc["parse_failures"], 1)

    def test_byte_offsets_not_line_numbers(self):
        """Gaps are measured in bytes. One line can be a huge tool result; line count says nothing."""
        with Fx([compact("one")] + padding(3) + [compact("two")]) as p:
            sc = vitals.scan(p)
        gap = sc["compacts"][1]["offset"] - sc["compacts"][0]["offset"]
        self.assertGreater(gap, 6000)


# ── Grading ─────────────────────────────────────────────────────────────────

class TestGrade(unittest.TestCase):

    def g(self, layers, **kw):
        m = {"layers": layers, "rapid": 0, "tail_bytes": 0, "format_suspect": False,
             "parse_failures": 0}
        m.update(kw)
        return vitals.grade(m)[0]

    def test_thresholds(self):
        self.assertEqual(self.g(0), "ok")
        self.assertEqual(self.g(2), "ok")     # boundary: 2 stays silent
        self.assertEqual(self.g(3), "warn")   # boundary: 3 starts warning
        self.assertEqual(self.g(5), "warn")
        self.assertEqual(self.g(6), "crit")   # boundary: 6 suggests a fresh session

    def test_rapid_escalates(self):
        self.assertEqual(self.g(1, rapid=2), "warn")   # ok   -> warn
        self.assertEqual(self.g(3, rapid=1), "crit")   # warn -> crit

    def test_format_suspect_escalates_and_speaks(self):
        """A vanished field must make noise. Silently reporting "healthy" is the worst failure."""
        level, _, mods = vitals.grade(
            {"layers": 0, "rapid": 0, "tail_bytes": 0,
             "format_suspect": True, "parse_failures": 0})
        self.assertNotEqual(level, "ok")
        self.assertTrue(any("format" in m for m in mods))

    def test_crit_cannot_overflow(self):
        self.assertEqual(self.g(9, rapid=5, format_suspect=True), "crit")


class TestPending(unittest.TestCase):

    def test_precompact_counts_the_pending_one(self):
        """
        At PreCompact time the compaction about to happen is not in the transcript yet.
        Without counting it explicitly, the pre-compaction warning is always one behind.
        """
        with Fx([compact("one"), compact("two")]) as p:
            sc = vitals.scan(p)
        self.assertEqual(vitals.metrics(sc, pending=0)["layers"], 2)
        self.assertEqual(vitals.metrics(sc, pending=1)["layers"], 3)


# ── Dangerous commands and credentials ──────────────────────────────────────

class TestDanger(unittest.TestCase):

    CFG = {"approval": "default", "danger_patterns": vitals.DEFAULT_DANGER_PATTERNS}

    def test_matches_destructive(self):
        for cmd in ["rm -rf build/", "git push --force origin main",
                    "git reset --hard HEAD~3", "sudo systemctl stop nginx",
                    "terraform destroy", "DROP TABLE users;"]:
            self.assertTrue(vitals.is_dangerous(cmd, self.CFG), cmd)

    def test_allows_ordinary(self):
        for cmd in ["ls -la", "git status", "npm test", "cat README.md",
                    "grep -rn TODO ."]:
            self.assertFalse(vitals.is_dangerous(cmd, self.CFG), cmd)

    def test_mode_off_and_all(self):
        self.assertFalse(vitals.is_dangerous("rm -rf /", {"approval": "off"}))
        self.assertTrue(vitals.is_dangerous("ls", {"approval": "all"}))


class TestSecretScan(unittest.TestCase):

    def test_catches_common_credentials(self):
        for text in [
            "token = sk-ant-api03-" + "A" * 40,
            "export GH=ghp_" + "b" * 36,
            "AKIAIOSFODNN7EXAMPLE",
            "-----BEGIN RSA PRIVATE KEY-----",
            'api_key: "' + "c" * 32 + '"',
        ]:
            self.assertTrue(vitals.scan_secrets(text), text[:30])

    def test_clean_text_passes(self):
        self.assertEqual(
            vitals.scan_secrets("Reworked the scanner to use byte offsets"), [])


# ── PROGRESS.md write contract ──────────────────────────────────────────────

class TestProgress(unittest.TestCase):

    CFG = {"progress_md": {"enabled": True, "max_bytes": 100_000,
                           "filename": "PROGRESS.md"}}

    def test_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            ok, why = vitals.write_progress(d, "s1", "body", {"progress_md": {}})
        self.assertFalse(ok)
        self.assertIn("not enabled", why)

    def test_refuses_to_write_secrets(self):
        """Writing into a project directory means it may get committed. Skip rather than leak."""
        with tempfile.TemporaryDirectory() as d:
            ok, why = vitals.write_progress(d, "s1", "key sk-ant-api03-" + "Z" * 40,
                                            self.CFG)
        self.assertFalse(ok)
        self.assertIn("credentials", why)

    def test_sessions_do_not_clobber_each_other(self):
        """Concurrent sessions each update only their own block."""
        with tempfile.TemporaryDirectory() as d:
            vitals.write_progress(d, "sessA", "first draft from A", self.CFG)
            vitals.write_progress(d, "sessB", "notes from B", self.CFG)
            vitals.write_progress(d, "sessA", "revised text from A", self.CFG)
            text = (Path(d) / "PROGRESS.md").read_text(encoding="utf-8")
        self.assertIn("revised text from A", text)
        self.assertIn("notes from B", text)         # B survives A's rewrite
        self.assertNotIn("first draft from A", text)  # A replaced only its own block
        self.assertEqual(text.count("session-vitals:sessA"), 2)  # begin and end markers

    def test_size_limit(self):
        cfg = {"progress_md": {"enabled": True, "max_bytes": 200,
                               "filename": "PROGRESS.md"}}
        with tempfile.TemporaryDirectory() as d:
            ok, why = vitals.write_progress(d, "s1", "x" * 5000, cfg)
        self.assertFalse(ok)
        self.assertIn("limit", why)


# ── Project directory resolution ────────────────────────────────────────────

class TestProjectDir(unittest.TestCase):
    """
    The hook payload's cwd is not the project directory. It follows the most recent
    shell operation (one real session reported four different values), and people
    routinely launch Claude Code from ~ while working on a project elsewhere.
    Guessing wrong means writing PROGRESS.md into someone's home directory.
    """

    def test_finds_repo_root_from_subdirectory(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "proj"
            (root / ".git").mkdir(parents=True)
            deep = root / "src" / "nested"
            deep.mkdir(parents=True)
            got, why = vitals.resolve_project_dir(str(deep))
        self.assertIsNotNone(got, why)
        self.assertEqual(got.name, "proj")

    def test_refuses_when_no_repo_found(self):
        with tempfile.TemporaryDirectory() as d:
            got, why = vitals.resolve_project_dir(d)
        self.assertIsNone(got)
        self.assertIn("no git repository", why)

    def test_refuses_home_directory(self):
        got, why = vitals.resolve_project_dir(str(Path.home()))
        self.assertIsNone(got)
        self.assertIn("home directory", why)

    def test_refuses_nonexistent_path(self):
        got, why = vitals.resolve_project_dir("/nonexistent/nope")
        self.assertIsNone(got)
        self.assertIn("not a directory", why)


class TestWorkspaceRoot(unittest.TestCase):
    """
    Deriving the target from every directory the session visited, rather than from
    whichever one it happened to be in last. The case that forced this: a workspace
    holding a frontend and a backend repository side by side, where the answer flipped
    between the two depending on when compaction fired.
    """

    def transcript(self, dirs):
        return Fx([json.dumps({"type": "user", "cwd": d}) for d in dirs])

    def test_multi_repo_workspace_resolves_to_the_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d) / "workspace"           # not a repository itself
            for repo in ("frontend", "backend"):
                (ws / repo / ".git").mkdir(parents=True)
            with self.transcript([str(ws), str(ws / "frontend"), str(ws / "backend")]) as p:
                got, why = vitals.workspace_root(p)
        self.assertIsNotNone(got, why)
        self.assertEqual(got.name, "workspace")

    def test_single_repo_still_resolves_to_the_repo_root(self):
        """The ancestor may be a subdirectory; the repository root is the better answer."""
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "proj"
            (repo / ".git").mkdir(parents=True)
            (repo / "src" / "api").mkdir(parents=True)
            (repo / "src" / "web").mkdir(parents=True)
            with self.transcript([str(repo / "src" / "api"), str(repo / "src" / "web")]) as p:
                got, why = vitals.workspace_root(p)
        self.assertIsNotNone(got, why)
        self.assertEqual(got.name, "proj")

    def test_launch_directory_does_not_drag_the_root_up(self):
        """
        Claude Code is routinely started from a directory holding many projects. That
        reading sits above the real one and pulled the answer up to the container - a
        real session on cube-master resolved to the parent holding every project.
        """
        with tempfile.TemporaryDirectory() as d:
            container = Path(d) / "Projects"
            repo = container / "cube-master"
            (repo / ".git").mkdir(parents=True)
            with self.transcript([str(container), str(repo)]) as p:
                got, why = vitals.workspace_root(p)
        self.assertIsNotNone(got, why)
        self.assertEqual(got.name, "cube-master")

    def test_dependency_trees_are_cut_away(self):
        """One cd into node_modules is not a statement about project structure."""
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "app"
            deep = repo / "node_modules" / ".pnpm" / "next@16" / "dist"
            deep.mkdir(parents=True)
            (repo / ".git").mkdir()
            with self.transcript([str(repo), str(deep)]) as p:
                got, why = vitals.workspace_root(p)
        self.assertIsNotNone(got, why)
        self.assertEqual(got.name, "app")

    def test_finds_this_sessions_transcript_from_the_environment(self):
        """
        CLAUDE_CODE_SESSION_ID *is* present for Bash tool calls, unlike
        $CLAUDE_PLUGIN_ROOT. That is what lets the commands resolve a project directory
        as precisely as the hooks do, instead of guessing from the shell's location.
        """
        saved_home, saved_env = Path.home, os.environ.get("CLAUDE_CODE_SESSION_ID")
        with tempfile.TemporaryDirectory() as d:
            sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            proj = Path(d) / ".claude" / "projects" / "-some-slug"
            proj.mkdir(parents=True)
            (proj / (sid + ".jsonl")).write_text("{}\n", encoding="utf-8")
            try:
                Path.home = staticmethod(lambda: Path(d))
                os.environ["CLAUDE_CODE_SESSION_ID"] = sid
                self.assertEqual(vitals.current_transcript(),
                                 str(proj / (sid + ".jsonl")))
                os.environ["CLAUDE_CODE_SESSION_ID"] = "../../etc/passwd"
                self.assertIsNone(vitals.current_transcript())   # not a session id
            finally:
                Path.home = saved_home
                if saved_env is None:
                    os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
                else:
                    os.environ["CLAUDE_CODE_SESSION_ID"] = saved_env

    def test_refuses_when_the_ancestor_is_too_broad(self):
        """A session that wandered across unrelated trees has no meaningful root."""
        with self.transcript([str(Path.home()), str(Path.home() / "a" / "b"),
                              str(Path.home() / "c")]) as p:
            got, why = vitals.workspace_root(p)
        self.assertIsNone(got)
        self.assertIn("too broad", why)

    def test_refuses_without_recorded_directories(self):
        with Fx([chat("no cwd anywhere in here")]) as p:
            got, why = vitals.workspace_root(p)
        self.assertIsNone(got)
        self.assertIn("no working directory", why)

    def test_ignores_relative_and_malformed_entries(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "proj"
            (repo / ".git").mkdir(parents=True)
            with self.transcript([str(repo), "relative/path", ""]) as p:
                got, why = vitals.workspace_root(p)
        self.assertIsNotNone(got, why)
        self.assertEqual(got.name, "proj")


# ── Hook output shape ───────────────────────────────────────────────────────

class TestHookOutput(unittest.TestCase):
    """
    Each event accepts a different output shape, and getting it wrong fails loudly at
    runtime ("Hook JSON output validation failed"). PostCompact in particular has no
    decision control at all - its output never reaches the model - so the checkpoint
    prompt has to ride on SessionStart with source=compact, which fires at the same
    moment and does support context injection.
    """

    ENABLED = {"progress_md": {"enabled": True, "max_bytes": 100_000,
                               "filename": "PROGRESS.md"}}

    def run_hook(self, fn, payload, cfg=None):
        saved = (vitals.load_config, vitals.notify, vitals.beat)
        buf = io.StringIO()
        try:
            if cfg is not None:
                vitals.load_config = lambda: cfg
            vitals.notify = lambda *a, **k: None   # no desktop popups during tests
            vitals.beat = lambda *a, **k: None     # never touch the real state file
            with redirect_stdout(buf):
                fn(payload)
        finally:
            vitals.load_config, vitals.notify, vitals.beat = saved
        text = buf.getvalue()
        return json.loads(text) if text.strip() else None

    def test_postcompact_stays_silent(self):
        """Output would be discarded, and emitting the wrong shape is a hard error."""
        self.assertIsNone(self.run_hook(vitals.hook_postcompact, {"session_id": "s1"},
                                        self.ENABLED))

    def test_compact_start_carries_the_checkpoint_prompt(self):
        out = self.run_hook(vitals.hook_sessionstart,
                            {"session_id": "s1", "source": "compact",
                             "transcript_path": "/nonexistent.jsonl"}, self.ENABLED)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "SessionStart")
        self.assertIn("write-progress", hso["additionalContext"])

    def test_prompt_names_a_runnable_script_path(self):
        """
        $CLAUDE_PLUGIN_ROOT is exported to hook processes only. A prompt carrying it
        expanded to "/vitals.py" and failed the moment a real session tried to obey:
        "can't open file '/vitals.py'". The path has to be absolute and already resolved.
        """
        out = self.run_hook(vitals.hook_sessionstart,
                            {"session_id": "s1", "source": "compact",
                             "transcript_path": "/nonexistent.jsonl"}, self.ENABLED)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("CLAUDE_PLUGIN_ROOT", ctx)
        self.assertIn(str(Path(vitals.__file__).resolve()), ctx)

    def test_other_sources_do_not(self):
        for source in ("resume", "startup", "clear"):
            self.assertIsNone(self.run_hook(
                vitals.hook_sessionstart,
                {"session_id": "s1", "source": source,
                 "transcript_path": "/nonexistent.jsonl"}, self.ENABLED), source)

    def test_disabled_config_suppresses_it(self):
        self.assertIsNone(self.run_hook(
            vitals.hook_sessionstart,
            {"session_id": "s1", "source": "compact",
             "transcript_path": "/nonexistent.jsonl"}, {"progress_md": {}}))

    def test_precompact_sends_no_context(self):
        """PreCompact cannot inject context, and it would be compacted away regardless."""
        with Fx([compact("a"), compact("b"), compact("c"), compact("d")]) as p:
            out = self.run_hook(vitals.hook_precompact,
                                {"session_id": "s1", "transcript_path": p}, self.ENABLED)
        self.assertIn("systemMessage", out)
        self.assertNotIn("hookSpecificOutput", out)


# ── Heartbeat ───────────────────────────────────────────────────────────────

class TestHeartbeat(unittest.TestCase):
    """
    A global heartbeat proves the plugin ran somewhere, which is not the question.
    Hook configuration is captured when a session starts, so a session older than the
    install runs nothing and reports nothing - and those are precisely the long sessions
    that need a checkpoint most. Recording the session id makes that detectable.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (vitals.STATE_DIR, vitals.STATE_PATH)
        vitals.STATE_DIR = Path(self.tmp.name)
        vitals.STATE_PATH = vitals.STATE_DIR / "state.json"

    def tearDown(self):
        vitals.STATE_DIR, vitals.STATE_PATH = self.saved
        self.tmp.cleanup()

    def state(self):
        return json.loads(vitals.STATE_PATH.read_text(encoding="utf-8"))

    def test_records_the_session_that_beat(self):
        vitals.beat("PreToolUse", "sess-a")
        self.assertIn("sess-a", self.state()["sessions"])

    def test_absent_session_id_is_not_recorded(self):
        vitals.beat("PreToolUse")
        self.assertEqual(self.state().get("sessions", {}), {})
        self.assertIn("PreToolUse", self.state()["heartbeat"])

    def test_forgets_the_oldest_sessions(self):
        for i in range(vitals.SESSION_MEMORY + 5):
            vitals.beat("PreToolUse", "sess-%03d" % i)
        self.assertEqual(len(self.state()["sessions"]), vitals.SESSION_MEMORY)


# ── Update checking ─────────────────────────────────────────────────────────

class TestUpdateCheck(unittest.TestCase):
    """
    The plugin manager cannot be trusted to notice a release: the install path is
    version-namespaced and the version string is pinned by hand. So the check is done
    here, following gstack's design - including the parts that exist because a naive
    version compare misfires.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.saved = (vitals.STATE_DIR, vitals.UPDATE_CACHE_PATH, vitals.SNOOZE_PATH,
                      vitals._fetch_remote_version, vitals.VERSION,
                      vitals.spawn_update_refresh)
        vitals.spawn_update_refresh = lambda: None   # no real subprocesses in tests
        vitals.STATE_DIR = d
        vitals.UPDATE_CACHE_PATH = d / "last-update-check"
        vitals.SNOOZE_PATH = d / "update-snoozed"
        self.calls = []
        vitals.VERSION = "1.2.0"

    def tearDown(self):
        (vitals.STATE_DIR, vitals.UPDATE_CACHE_PATH, vitals.SNOOZE_PATH,
         vitals._fetch_remote_version, vitals.VERSION,
         vitals.spawn_update_refresh) = self.saved
        self.tmp.cleanup()

    def remote(self, value):
        def fake():
            self.calls.append(value)
            return value
        vitals._fetch_remote_version = fake

    def test_newer_remote_is_an_upgrade(self):
        self.remote("1.3.0")
        self.assertEqual(vitals.check_update({})[0], "upgrade")

    def test_older_remote_is_not(self):
        """
        A stale CDN, or a local checkout running ahead of the branch, would otherwise
        produce a backwards "upgrade available" pointing at the version already replaced.
        """
        self.remote("1.1.9")
        self.assertEqual(vitals.check_update({})[0], "current")

    def test_version_ordering_is_numeric_not_lexical(self):
        self.assertGreater(vitals._vtuple("1.10.0"), vitals._vtuple("1.9.0"))
        self.assertGreater(vitals._vtuple("2.0"), vitals._vtuple("1.999.999"))

    def test_unreachable_source_reports_current(self):
        """Silence on failure. A version check must never be why a session feels broken."""
        vitals._fetch_remote_version = lambda: None
        state, local, remote = vitals.check_update({})
        self.assertEqual(state, "current")
        self.assertIsNone(remote)

    def test_fresh_cache_skips_the_network(self):
        self.remote("1.3.0")
        vitals.check_update({})
        vitals.check_update({})
        self.assertEqual(len(self.calls), 1)

    def test_offline_mode_never_fetches(self):
        """The hooks read the cache only; session start must not wait on the network."""
        self.remote("1.3.0")
        vitals.check_update({}, network=False)
        self.assertEqual(self.calls, [])

    def test_disabled_in_config(self):
        self.remote("1.3.0")
        self.assertEqual(vitals.check_update({"update_check": False})[0], "unknown")
        self.assertEqual(self.calls, [])

    def test_snooze_escalates_then_caps(self):
        self.assertEqual(vitals.snooze("1.3.0"), 24 * 3600)
        self.assertEqual(vitals.snooze("1.3.0"), 48 * 3600)
        self.assertEqual(vitals.snooze("1.3.0"), 7 * 24 * 3600)
        self.assertEqual(vitals.snooze("1.3.0"), 7 * 24 * 3600)   # capped
        self.assertTrue(vitals.is_snoozed("1.3.0"))

    def test_a_new_version_resets_the_snooze(self):
        """Declining 1.3.0 is not declining 1.4.0."""
        vitals.snooze("1.3.0")
        self.assertTrue(vitals.is_snoozed("1.3.0"))
        self.assertFalse(vitals.is_snoozed("1.4.0"))
        self.assertEqual(vitals.snooze("1.4.0"), 24 * 3600)

    def test_expired_snooze_speaks_again(self):
        vitals.SNOOZE_PATH.write_text("1.3.0 1 0", encoding="utf-8")   # epoch 0
        self.assertFalse(vitals.is_snoozed("1.3.0"))

    def test_corrupt_snooze_file_is_ignored(self):
        vitals.SNOOZE_PATH.write_text("garbage", encoding="utf-8")
        self.assertFalse(vitals.is_snoozed("1.3.0"))

    def test_notice_is_silent_while_snoozed(self):
        self.remote("1.3.0")
        vitals.check_update({})
        vitals.snooze("1.3.0")
        self.assertIsNone(vitals.update_notice({}))


# ── Platform capability ─────────────────────────────────────────────────────

class TestPlatform(unittest.TestCase):

    def test_approval_requires_macos(self):
        """Off macOS, approval must report unavailable so the hook skips instead of blocking."""
        original = vitals.IS_MACOS
        try:
            vitals.IS_MACOS = False
            self.assertFalse(vitals.approval_available())
        finally:
            vitals.IS_MACOS = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
