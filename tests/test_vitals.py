"""
Tests for session-vitals. Standard library unittest, no pytest: the project declares
Python 3.8+ as its only dependency, and the test suite should not add a second one.

    python3 -m unittest discover -s tests -v

Every fixture is synthetic. Real transcripts contain full conversations, project paths
and possibly pasted credentials, so they never enter the repository. The counting logic
only cares about fields and offsets, which synthetic data covers fine.
"""

import json
import sys
import tempfile
import unittest
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
