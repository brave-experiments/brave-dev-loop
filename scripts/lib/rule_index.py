"""The rules in a project's best-practices docs, as a reviewer can cite them.

A rule is a `##` heading with an `<a id="XX-NNN"></a>` anchor on one of the four
lines above it, the same shape chunk-best-practices.py splits on, so what is
listed here is what the detect passes were given. A rule with no anchor cannot be
linked and is left out.
"""

import os
import re

_ANCHOR = re.compile(r'^<a id="([A-Za-z0-9_-]+)"></a>\s*$')
_LINK = re.compile(r"/([^/]+\.md)#([A-Za-z0-9_-]+)$")
SUMMARY_CHARS = 240


def _summary(lines):
    """The first paragraph under a heading, flattened and cut at a word."""
    words = []
    for line in lines:
        text = line.strip()
        if not text:
            if words:
                break
            continue
        if text.startswith(("#", "<!--", "---", "<a id=")):
            if words:
                break
            continue
        words.append(text)
    flat = re.sub(r"[*_`]", "", " ".join(words))
    if len(flat) <= SUMMARY_CHARS:
        return flat
    return flat[:SUMMARY_CHARS].rsplit(" ", 1)[0] + "..."


def rules_in(path):
    """[{"id", "heading", "summary"}] for each anchored rule in one doc."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
    except OSError:
        return []
    rules = []
    for i, line in enumerate(lines):
        if not re.match(r"^## [^#]", line):
            continue
        anchor = None
        for j in range(max(0, i - 4), i):
            m = _ANCHOR.match(lines[j])
            if m:
                anchor = m.group(1)
        if anchor is None:
            continue
        rules.append(
            {
                "id": anchor,
                "heading": re.sub(r"^#+\s*", "", line).strip(),
                "summary": _summary(lines[i + 1 :]),
            }
        )
    return rules


def index_for(bp_dir, docs, link_base):
    """[{"url", "heading", "summary"}] for every rule in the named docs."""
    index = []
    for doc in dict.fromkeys(docs):
        for rule in rules_in(os.path.join(bp_dir, doc)):
            index.append(
                {
                    "url": f"{link_base}/{doc}#{rule['id']}",
                    "heading": rule["heading"],
                    "summary": rule["summary"],
                }
            )
    return index


def all_headings(bp_dir):
    """The heading of every anchored rule in every doc in `bp_dir`."""
    try:
        names = sorted(n for n in os.listdir(bp_dir) if n.endswith(".md"))
    except OSError:
        return []
    return [r["heading"] for n in names for r in rules_in(os.path.join(bp_dir, n))]


def heading_of(rule_link, bp_dir):
    """The heading of the rule a link names in `bp_dir`, or None when it names none."""
    m = _LINK.search(rule_link or "")
    if not m:
        return None
    doc, anchor = m.groups()
    for rule in rules_in(os.path.join(bp_dir, doc)):
        if rule["id"] == anchor:
            return rule["heading"]
    return None


def names_no_rule(rule_link, bp_dir):
    """True when the link points into a doc that exists but has no such anchor.

    A link that cannot be read (no doc, or not a link to a rule doc) is not
    judged: only a doc the project keeps, and an anchor it lacks, show a made-up id.
    """
    m = _LINK.search(rule_link or "")
    if not m:
        return False
    doc, anchor = m.groups()
    path = os.path.join(bp_dir, doc)
    if not os.path.isfile(path):
        return False
    return anchor not in {r["id"] for r in rules_in(path)}
