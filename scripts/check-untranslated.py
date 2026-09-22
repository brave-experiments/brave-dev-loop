#!/usr/bin/env python3
"""Find the sentences a branch added in source, outside the message catalog.

A string written in the source is a string no translation can reach. It ships in
one language, and where the words around it come from a catalog the reader gets
half a sentence in their own.

    python3 scripts/check-untranslated.py --diff-base upstream/main
    python3 scripts/check-untranslated.py --diff-base upstream/main --strict

Run it from the worktree the branch lives in. It reads the lines this branch
added, committed or not, and prints the sentence-shaped literals among them in
the source the project's profile says it localizes. A project whose profile
declares no `localization` block has nothing checked here.

Which of them is a defect is not something a pattern can decide: a program says
things to a person, which belong in a catalog, and things to a machine, which
must not be translated at all. So these print as warnings and exit 0 unless
--strict. Accounting for each one is the author's, and saying in the pull
request body which were left in the source and why is what makes that visible.
"""

import argparse
import fnmatch
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.load_config import load_config, load_profile

# Stands in for the line numbers of a file with no earlier version to compare
# against, every line of which is new. Not a set of every number, and not None:
# None is already what a run that could not read the diff at all returns.
EVERY_LINE = object()


# A double-quoted literal, escapes included. Rust raw strings and C++ raw
# literals are not matched: both are overwhelmingly test fixtures and JSON, and
# missing one is cheaper here than a finding on every fixture.
LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)+)"')

# Strings written for whoever is debugging, not for anyone using the program. A
# line holding one of these is skipped whole: the message and the condition it
# reports are usually on it together.
DEVELOPER = re.compile(
    r"\b(?:expect|expect_err|panic!|unreachable!|todo!|unimplemented!|"
    r"assert\w*|debug_assert\w*|env!|include_str!|include_bytes!|cfg!|dbg!|"
    r"NOTREACHED|NOTIMPLEMENTED|DCHECK\w*|CHECK\w*|DLOG|DVLOG|VLOG)\b"
)

# Where the unit tests start, in a Rust file. Everything from here down is test
# code, which says what it likes in whatever language the assertions are in.
TESTS_BEGIN = re.compile(r"^\s*(?:#\[cfg\(test\)\]|mod tests\b)")

# The words English glues sentences together with. A literal holding one of
# these is prose; one holding none is a path, a command, a key or an identifier.
# This is the whole of what separates "is empty" from "git diff --stat", so it
# is deliberately short -- every word here is one no command line spells.
FUNCTION_WORDS = set(
    "a an the this that these those it its you your they their there here "
    "is are was were be been has have had will would can cannot could does "
    "do did no not nothing none of to in on for with without and or but "
    "than then so as at by from into what which who when while why how "
    "already still yet only every each any too".split()
)


def is_prose(text):
    """Whether a literal reads as a sentence rather than as a value.

    Two words and one piece of English glue between them. Short on purpose: the
    string this check exists for is `is empty`, so a rule wanting three words or
    twelve characters would have missed the defect that prompted it.
    """
    text = text.strip()
    if len(text) < 6 or " " not in text:
        return False
    # A single letter counts as a word: `a` is the commonest glue English has,
    # and a rule that dropped it misses "needs a domain" and "is not a rule".
    words = re.findall(r"[A-Za-z']+", text)
    if len(words) < 2:
        return False
    return bool({word.lower().strip("'") for word in words} & FUNCTION_WORDS)


def prose_lines(source):
    """The prose literals in one file, as {line number: [text, ...]}.

    Read from the whole file rather than from the diff's added lines, because
    whether a line is inside a comment or below the tests is not visible in a
    hunk.
    """
    found = {}
    in_block_comment = False
    for number, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if in_block_comment:
            if "*/" in stripped:
                in_block_comment = False
            continue
        if stripped.startswith("/*"):
            in_block_comment = "*/" not in stripped
            continue
        if TESTS_BEGIN.match(line):
            break
        if stripped.startswith(("//", "#[", "#!")) or DEVELOPER.search(line):
            continue
        prose = [text for text in LITERAL.findall(line) if is_prose(text)]
        if prose:
            found[number] = prose
    return found


def matching(paths, patterns):
    """The paths a pattern claims. A pattern with no glob character matches any
    path beneath it, so `crates/` need not be written `crates/*`. Same reading
    as the profile's uiPaths in check-pr-body.py."""
    hit = []
    for path in paths:
        for pattern in patterns:
            if any(char in pattern for char in "*?["):
                claimed = fnmatch.fnmatch(path, pattern)
            else:
                claimed = path.startswith(pattern)
            if claimed:
                hit.append(path)
                break
    return hit


def added_lines(base):
    """{path: {line number, ...}} for the lines this branch added, or None.

    Measured from the merge base to the working tree, so a string written but
    not yet committed is caught by the same run -- the point is to find it
    before the pull request, and half the work is uncommitted when the gates
    start. Line numbers are the file's as it stands, which is what the reader of
    a finding will open.
    """
    merge_base = subprocess.run(
        ["git", "merge-base", base, "HEAD"], capture_output=True, text=True
    )
    if merge_base.returncode != 0:
        sys.stderr.write(merge_base.stderr)
        return None
    diff = subprocess.run(
        ["git", "diff", "--unified=0", "--no-color", merge_base.stdout.strip()],
        capture_output=True,
        text=True,
    )
    if diff.returncode != 0:
        sys.stderr.write(diff.stderr)
        return None
    added = added_from_diff(diff.stdout)

    # A file written but never added is in no diff at all, and a new module is
    # where a new message is most likely to be. Every line of one is new.
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
    )
    for path in untracked.stdout.splitlines():
        if path.strip():
            added.setdefault(path.strip(), EVERY_LINE)
    return added


def added_from_diff(diff):
    """{path: {line number, ...}} from a `git diff --unified=0`."""
    added = {}
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            name = line[4:].strip()
            path = None if name == "/dev/null" else name.split("/", 1)[-1]
            continue
        if line.startswith("@@") and path:
            hunk = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if hunk:
                start = int(hunk.group(1))
                count = 1 if hunk.group(2) is None else int(hunk.group(2))
                added.setdefault(path, set()).update(range(start, start + count))
    return added


def findings(added, rules, read):
    """Every prose literal on a line this branch added, as (path, line, text).

    `read` returns a file's text, or None where it cannot be read -- a file the
    branch deleted, or one that is not text.
    """
    claimed = matching(sorted(added), rules.get("paths") or [])
    exempt = set(matching(claimed, rules.get("exempt") or []))
    out = []
    for path in claimed:
        if path in exempt:
            continue
        source = read(path)
        if source is None:
            continue
        prose = prose_lines(source)
        lines = (
            prose.keys() if added[path] is EVERY_LINE else added[path] & prose.keys()
        )
        for number in sorted(lines):
            for text in prose[number]:
                out.append((path, number, text))
    return out


def from_disk(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(
        description="Find sentences a branch added in source rather than in the "
        "message catalog."
    )
    ap.add_argument(
        "--diff-base",
        required=True,
        help="the ref this branch is measured against, e.g. upstream/main. Run "
        "from the worktree the branch lives in",
    )
    ap.add_argument(
        "--strict", action="store_true", help="exit 1 when anything is found"
    )
    args = ap.parse_args()

    rules = load_profile(load_config()).get("localization") or {}
    if not rules:
        print("This project declares no localization, so no string is checked.")
        return 0

    added = added_lines(args.diff_base)
    if added is None:
        print(
            f"error: cannot diff against {args.diff_base}. Run this from the "
            "project worktree, or fetch the ref first."
        )
        return 2

    found = findings(added, rules, from_disk)
    if not found:
        print("No sentence was added in source this project localizes.")
        return 0

    print(f"{len(found)} sentence(s) this branch added in source:\n")
    for path, number, text in found:
        print(f'  {path}:{number}  "{text}"')

    catalog = rules.get("catalog")
    if catalog and catalog not in added:
        print(f"\nThis branch does not change {catalog}.")

    print(
        "\nEach one is either something a person reads or something a machine "
        "reads.\nA person's words belong in the catalog: "
        f"{rules.get('how') or 'put them where this project keeps its messages'}"
        "\nA machine's stay in the source. Say which in the pull request body, "
        "and why."
    )
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
