# Project progress

_Maintained by session-vitals, one block per session._

<!-- session-vitals:4ea67777-a62a-4c2b-b5ad-a76fab36 -->
_updated 2026-07-27T09:44:05+00:00_

Shipped the plugin end to end: single-file vitals.py, hooks wired for
PreCompact/PostCompact/SessionStart/PreToolUse, 26 tests, English throughout.

Verified by hand: approval Allow, Deny and 60s timeout all behave correctly.
Custom notification icons were investigated and rejected (ad-hoc signed apps
cannot register for notification permission on macOS 26).

Open: PostCompact has never fired on a real compaction yet.
<!-- /session-vitals:4ea67777-a62a-4c2b-b5ad-a76fab36 -->
