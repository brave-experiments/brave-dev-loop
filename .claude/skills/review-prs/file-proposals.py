#!/usr/bin/env python3
"""File the best-practice proposals a review's validators wrote, as issues.

A validator that saw a rule the project lacks describes it in `proposals`; this
script, which runs where the GitHub token is, turns it into one issue. It never
edits the best-practices docs: a maintainer decides whether the issue becomes a
rule. Every field came from a model that read an author's code and words, so the
text is cleaned before it is posted: no mentions, no links off the project's own
repository, bounded lengths.

collect-results.py calls file_proposals(); nothing else runs this file.
"""

import json
import os
import re
import subprocess  # nosemgrep
import sys
from urllib.parse import quote

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
sys.path.insert(0, os.path.join(BOT_DIR, "scripts"))
from lib.rule_index import all_headings

PREFIX = "Best practice: "
MARKER = "<!-- review-prs best-practice proposal -->"
MAX_PER_PR = 1
MAX_PER_RUN = 3
MAX_EVIDENCE = 3
SIMILAR = 0.6
LIMITS = {"title": 80, "doc": 60, "rule": 800, "why": 800, "note": 200}

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_URL = re.compile(r"https?://\S+")
_MENTION = re.compile(r"(?<![\w`/])@([A-Za-z0-9][A-Za-z0-9_-]*(?:/[A-Za-z0-9_.-]+)?)")
_WORDS = re.compile(r"[a-z0-9]+")
_FILLER = frozenset(
    "a an the of to in on for and or is are be it its that this with as by at from".split()
)


def log(msg):
    print(msg, file=sys.stderr)


def clean(text, limit, repo, one_line=False):
    """Plain text safe to post: no control characters, mentions or foreign links."""
    if not isinstance(text, str):
        return ""
    text = _CONTROL.sub("", text.replace("\r\n", "\n"))
    if one_line:
        text = " ".join(text.split())
    else:
        text = "\n".join(line.rstrip() for line in text.strip().split("\n"))
    own = f"https://github.com/{repo}/"
    text = _URL.sub(
        lambda m: m.group(0) if m.group(0).startswith(own) else "[link removed]", text
    )
    text = _MENTION.sub(r"`@\1`", text)
    return text[:limit].rstrip()


def _evidence(raw, repo):
    """[{"file", "line", "note"}] for the entries that name a plain path and a line."""
    kept = []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            continue
        path, line = e.get("file"), e.get("line")
        if not isinstance(path, str) or not isinstance(line, int) or line < 1:
            continue
        path = path.strip()
        if (
            not path
            or path.startswith(("/", "."))
            or ".." in path.split("/")
            or re.search(r"\s", path)
            or _CONTROL.search(path)
        ):
            continue
        kept.append(
            {
                "file": path,
                "line": line,
                "note": clean(e.get("note"), LIMITS["note"], repo, one_line=True),
            }
        )
    return kept[:MAX_EVIDENCE]


def normalize(raw, repo):
    """One proposal as plain cleaned text, or None when it is not a rule."""
    if not isinstance(raw, dict):
        return None
    p = {
        "title": clean(raw.get("title"), LIMITS["title"], repo, one_line=True),
        "doc": clean(raw.get("doc"), LIMITS["doc"], repo, one_line=True),
        "rule": clean(raw.get("rule"), LIMITS["rule"], repo),
        "why": clean(raw.get("why"), LIMITS["why"], repo),
        "evidence": _evidence(raw.get("evidence"), repo),
    }
    if len(p["title"]) < 12 or len(p["rule"]) < 20 or len(p["why"]) < 20:
        return None
    return p


def _tokens(text):
    return {w for w in _WORDS.findall(text.lower()) if w not in _FILLER}


def similar(a, b):
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= SIMILAR


def build_issue(p, pr, pr_repo):
    """(title, body) for a proposal made while reviewing `pr`."""
    number = pr["number"]
    url = f"https://github.com/{pr_repo}/pull/{number}"
    sha = pr.get("headRefOid", "")
    body = [
        MARKER,
        f"Proposed while reviewing [PR #{number}]({url}). A maintainer decides "
        "whether this becomes a rule; nothing has been added to the docs.",
        "",
        "## Proposed rule",
        "",
        p["rule"],
        "",
        "## Why a reviewer has to decide it",
        "",
        p["why"],
    ]
    if p["doc"]:
        body += ["", f"Suggested home: `{p['doc']}`."]
    if p["evidence"]:
        body += ["", "## Where it showed up", ""]
        for e in p["evidence"]:
            where = f"{e['file']}:{e['line']}"
            if sha:
                where = (
                    f"[`{where}`](https://github.com/{pr_repo}/blob/{sha}/"
                    f"{quote(e['file'])}#L{e['line']})"
                )
            else:
                where = f"`{where}`"
            body.append(f"- {where}" + (f": {e['note']}" if e["note"] else ""))
    return PREFIX + p["title"], "\n".join(body) + "\n"


def duplicate_of(p, pr, pr_repo, issues, headings):
    """Why this proposal should not be filed, or None."""
    for heading in headings:
        if similar(p["title"], heading):
            return f"the rule '{heading}' already exists"
    pull = re.compile(rf"github\.com/{re.escape(pr_repo)}/pull/{pr['number']}(?!\d)")
    for issue in issues:
        title = issue.get("title") or ""
        if not title.lower().startswith(PREFIX.lower()):
            continue
        if pull.search(issue.get("body") or ""):
            return f"{issue.get('url')} already proposes a rule from this pull request"
        if similar(p["title"], title[len(PREFIX) :]):
            return f"{issue.get('url')} already proposes it"
    return None


def _gh(argv, timeout=60):
    """(returncode, stdout, stderr) of a `gh` command."""
    result = subprocess.run(  # nosemgrep
        argv, capture_output=True, text=True, timeout=timeout
    )
    return result.returncode, result.stdout, result.stderr


def existing_proposals(issue_repo, run):
    """The repo's open and closed proposal issues, or None when GitHub would not list them."""
    rc, out, err = run(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            issue_repo,
            "--state",
            "all",
            "--search",
            '"Best practice" in:title',
            "--limit",
            "100",
            "--json",
            "number,title,url,body",
        ]
    )
    if rc != 0:
        log(f"WARNING: could not list issues in {issue_repo}: {err.strip()}")
        return None
    try:
        issues = json.loads(out)
    except json.JSONDecodeError:
        return None
    return issues if isinstance(issues, list) else None


def file_proposals(prs, pr_repo, issue_repo, bp_dir, run=_gh, dry_run=False, say=log):
    """File one issue for each PR's first valid proposal. Returns what became of each.

    `prs` is [{"number", "title", "headRefOid", "proposals": [raw, ...]}]. At most
    MAX_PER_PR per PR and MAX_PER_RUN in all; a proposal the docs or an earlier
    issue already has is not filed again. A failure is reported and never raised:
    the review has been posted by the time this runs.
    """
    wanted = []
    for pr in prs:
        for raw in (pr.get("proposals") or [])[: MAX_PER_PR * 3]:
            p = normalize(raw, pr_repo)
            if p:
                wanted.append((pr, p))
                break
    if not wanted:
        return []
    issues = existing_proposals(issue_repo, run)
    if issues is None:
        say("PROPOSAL: not filed, because existing issues could not be checked")
        return []
    headings = all_headings(bp_dir)
    outcomes = []
    filed = 0
    for pr, p in wanted:
        number = pr["number"]
        title, body = build_issue(p, pr, pr_repo)
        why_not = duplicate_of(p, pr, pr_repo, issues, headings)
        if why_not:
            say(f"PROPOSAL: PR #{number} '{p['title']}' not filed: {why_not}")
            outcomes.append({"pr": number, "status": "duplicate", "reason": why_not})
            continue
        if filed >= MAX_PER_RUN:
            say(
                f"PROPOSAL: PR #{number} '{p['title']}' not filed: {MAX_PER_RUN} filed this run"
            )
            outcomes.append({"pr": number, "status": "capped"})
            continue
        if dry_run:
            say(
                f"PROPOSAL: PR #{number} would file '{title}' (not filed: not an auto run)"
            )
            outcomes.append({"pr": number, "status": "dry-run", "title": title})
            continue
        rc, out, err = run(
            [
                "gh",
                "issue",
                "create",
                "--repo",
                issue_repo,
                "--title",
                title,
                "--body",
                body,
            ]
        )
        if rc != 0:
            say(f"WARNING: PROPOSAL: PR #{number} issue not created: {err.strip()}")
            outcomes.append({"pr": number, "status": "failed", "reason": err.strip()})
            continue
        url = (out.strip().splitlines() or [""])[-1]
        filed += 1
        issues.append({"title": title, "url": url, "body": body})
        say(f"PROPOSAL: PR #{number} filed {url}")
        outcomes.append({"pr": number, "status": "filed", "url": url, "title": title})
    return outcomes
