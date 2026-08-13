#!/usr/bin/env python3
"""
Bump the version in the three files that declare it.

Three copies exist because Claude Code reads two of them and the plugin reports the
third, and nothing keeps them in step on its own - so a release either moves all three
or the manifests disagree. A test asserts they match; this is what makes them match.

    python3 scripts/bump-version.py patch|minor|major
    python3 scripts/bump-version.py 2.0.0

Prints the new version on stdout and nothing else, so CI can capture it.
"""

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
MARKET = ROOT / ".claude-plugin" / "marketplace.json"
VITALS = ROOT / "vitals.py"


def current():
    return json.loads(PLUGIN.read_text(encoding="utf-8"))["version"]


def bump(version, level):
    if re.fullmatch(r"\d+\.\d+\.\d+", level):
        return level          # an explicit version, for a release that skips a step
    major, minor, patch = (int(p) for p in version.split("."))
    if level == "major":
        return "%d.0.0" % (major + 1)
    if level == "minor":
        return "%d.%d.0" % (major, minor + 1)
    if level == "patch":
        return "%d.%d.%d" % (major, minor, patch + 1)
    raise SystemExit("level must be major, minor, patch, or an explicit X.Y.Z")


def write(new):
    for path in (PLUGIN, MARKET):
        data = json.loads(path.read_text(encoding="utf-8"))
        if "version" in data:
            data["version"] = new
        for entry in data.get("plugins", []):
            entry["version"] = new
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    text = VITALS.read_text(encoding="utf-8")
    text, count = re.subn(r'^VERSION = "[^"]+"', 'VERSION = "%s"' % new, text, count=1,
                          flags=re.M)
    if count != 1:
        raise SystemExit("could not find VERSION in vitals.py")
    VITALS.write_text(text, encoding="utf-8")


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.strip())
    new = bump(current(), sys.argv[1])
    write(new)
    print(new)


if __name__ == "__main__":
    main()
