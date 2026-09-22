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

Nothing about any one language is written down here. The profile says how the
project quotes a string, what starts a comment, which calls take a developer's
words and where its tests begin, because a check built around Rust's spelling
would report noise on a codebase that writes `LOG(ERROR) << "..."` and see
nothing at all in one that quotes with `'`.

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

# Only git, with argument lists and no shell, so the ref a caller names is an
# argument to `git` and can never be a command.
import subprocess  # nosemgrep
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.load_config import load_config, load_profile

# Stands in for the line numbers of a file with no earlier version to compare
# against, every line of which is new. Not a set of every number, and not None:
# None is already what a run that could not read the diff at all returns.
EVERY_LINE = object()


# How much of a wrapped statement is kept when deciding whether a string belongs
# to whoever is debugging. Chromium writes `LOG(ERROR) << "..."` over two or
# three lines, so the macro is not on the line the string is on. The cap is what
# makes this safe where a language ends a statement with a newline rather than a
# `;`: without it one logging call would silence every line after it.
STATEMENT_LINES = 6

# Block comments are the one piece of syntax not left to the profile: Rust, C++,
# Objective-C, Java, Swift and TypeScript -- every language this bot localizes --
# all spell them this way.
BLOCK_COMMENT_OPEN = "/*"
BLOCK_COMMENT_CLOSE = "*/"

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


def literal_pattern(quotes):
    """A regex matching a string literal in each quote style a profile declares.

    Escapes are consumed, so a quote inside a string does not end it. A literal
    spanning a newline is not matched, which loses a wrapped template literal and
    keeps the scan line-oriented -- the line number is what a finding is read at.
    """
    return re.compile(
        "|".join(
            rf"{re.escape(q)}((?:[^{re.escape(q)}\\\n]|\\.)+){re.escape(q)}"
            for q in quotes
        )
    )


def developer_pattern(names):
    """A regex matching a call that takes a developer's words, not a reader's.

    A name ending in `*` is a prefix, so `assert*` covers `assert_eq!` and
    `assert_ne!` -- the same reading `uiPaths` gives a bare path. A name ending in
    punctuation is matched as written, because a word boundary after `!` never
    holds: the built-in list this replaced named eight macros it could not match.
    A name is never matched mid-identifier, so `assert*` leaves `debug_assert!`
    to a profile that lists it.
    """
    if not names:
        return None
    parts = []
    for name in names:
        if name.endswith("*"):
            parts.append(re.escape(name[:-1]) + r"\w*")
        elif name[-1:].isalnum() or name.endswith("_"):
            parts.append(re.escape(name) + r"\b")
        else:
            parts.append(re.escape(name))
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + ")")


class Lexer:
    """The language facts a profile declares, compiled.

    None of this is built into the check. bravebot quotes with `"`, comments with
    `//` and ends a statement with `;`; the next project spells all three
    differently, and a check that assumed one of them would read exactly one
    codebase. A profile that declares only where it localizes gets `"` and `//`,
    which is every language this bot targets, and no exemptions it did not ask
    for.
    """

    def __init__(self, rules):
        self.literal = literal_pattern(rules.get("quotes") or ['"'])
        self.comments = tuple(rules.get("comments") or ["//"])
        self.developer = developer_pattern(rules.get("developer") or ())
        begin = rules.get("testsBegin")
        self.tests_begin = re.compile(begin) if begin else None

    def literals(self, line):
        """Every string literal on one line, whichever declared quote holds it."""
        return [found.group(found.lastindex) for found in self.literal.finditer(line)]

    def code(self, line):
        """The line with its string literals removed.

        A name is looked for in the code and never in the words being checked, or
        the message `what the planner expects a slot to hold` suppresses itself:
        `expect*` matches `expects` in the prose, and the finding disappears for
        containing an English word.
        """
        return self.literal.sub("", line)


def prose_lines(source, lex):
    """The prose literals in one file, as {line number: [text, ...]}.

    Read from the whole file rather than from the diff's added lines, because
    whether a line is inside a comment or below the tests is not visible in a
    hunk.
    """
    found = {}
    in_block_comment = False
    statement = []
    for number, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if in_block_comment:
            if BLOCK_COMMENT_CLOSE in stripped:
                in_block_comment = False
            continue
        if stripped.startswith(BLOCK_COMMENT_OPEN):
            in_block_comment = BLOCK_COMMENT_CLOSE not in stripped
            continue
        if lex.tests_begin and lex.tests_begin.match(line):
            break
        if stripped.startswith(lex.comments):
            continue

        # The statement so far, not the line: the macro that owns a string is
        # often above it.
        statement.append(lex.code(line))
        del statement[:-STATEMENT_LINES]
        prose = [text for text in lex.literals(line) if is_prose(text)]
        if prose and not (lex.developer and lex.developer.search("\n".join(statement))):
            found[number] = prose
        if not stripped or stripped[-1] in ";{}":
            statement = []
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
    lex = Lexer(rules)
    out = []
    for path in claimed:
        if path in exempt:
            continue
        source = read(path)
        if source is None:
            continue
        prose = prose_lines(source, lex)
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
