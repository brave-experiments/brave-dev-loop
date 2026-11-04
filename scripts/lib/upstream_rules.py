"""Chromium's agentic-review rules, as the review pipeline can use them.

Chromium keeps what its AI reviewer knows under agents/knowledge_base/rules/:

- one markdown file per rule, with `name`, `rubric` and `severity` in a
  frontmatter block, and the rule itself below it;
- a COMMON_METADATA textproto per directory whose `capability_groups` say when
  the rules apply. A group lists `rules`, and may carry `path_expression`
  regexes over a changed file's path and `content_patterns` regexes over what
  the change adds. A group with neither applies to every file.

Everything here is deterministic and costs no model tokens: the metadata decides
which rules a diff meets, the same way chunk-best-practices.py splits our own
docs. The rules are a second source next to the project's best-practices docs;
nothing is merged into them.

A rule is linked as `<linkBase>/<path>.md#<file name>`. The fragment names no
anchor on the page; it exists so the link has the `<doc>.md#<id>` shape the rest
of the pipeline reads, and so the id can be checked against the rules on disk.
"""

import os
import re
import sys

# Highest first. `optional` is Chromium's own "take it or leave it".
SEVERITIES = ("blocking", "should_fix", "nit", "optional")
DEFAULT_MIN_SEVERITY = "should_fix"
# Our severities are high/medium/low (post-review.py). An upstream rule has not
# been vetted against this project, so by default none of them is `high`: a
# finding that high would stop the pull request from being approved.
DEFAULT_SEVERITY_MAP = {
    "blocking": "medium",
    "should_fix": "low",
    "nit": "low",
    "optional": "low",
}

RULES_PREFIX = "agents/knowledge_base/rules/"
METADATA_FILE = "COMMON_METADATA"
_LINK = re.compile(r"/([^/]+)\.md#([A-Za-z0-9_-]+)$")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "\\": "\\"}


def _warn(message):
    print(f"WARNING: upstream rules: {message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# textproto: the subset COMMON_METADATA uses
# ---------------------------------------------------------------------------


def _unescape(raw):
    out = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            if nxt in _ESCAPES:
                out.append(_ESCAPES[nxt])
            else:
                # `\.` and the like are regex, not a textproto escape: keep both.
                out.append(ch + nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_TOKEN = re.compile(
    r"""\s+|\#[^\n]*                      # whitespace, comments
    |(?P<str>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
    |(?P<id>[A-Za-z_][A-Za-z0-9_]*)
    |(?P<punct>[{}:;,<>\[\]])
    |(?P<num>[-+]?[0-9][0-9A-Za-z_.+-]*)""",
    re.VERBOSE,
)


def _tokens(text):
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise ValueError(f"unexpected {text[pos : pos + 20]!r} at offset {pos}")
        pos = m.end()
        if m.lastgroup == "str":
            yield "str", _unescape(m.group("str")[1:-1])
        elif m.lastgroup:
            yield m.lastgroup, m.group(m.lastgroup)


def parse_textproto(text):
    """A message as `{field: [value, ...]}`; a nested message is another such dict.

    Every field is a list because any field of a textproto may repeat. Adjacent
    string literals concatenate, as they do in the format itself.
    """
    toks = list(_tokens(text))
    pos = 0

    def message(closing):
        nonlocal pos
        fields = {}
        while pos < len(toks):
            kind, value = toks[pos]
            if kind == "punct" and value == "}":
                if not closing:
                    raise ValueError("unmatched }")
                pos += 1
                return fields
            if kind == "punct" and value in ";,":
                pos += 1
                continue
            if kind != "id":
                raise ValueError(f"expected a field name, got {value!r}")
            pos += 1
            if pos < len(toks) and toks[pos] == ("punct", ":"):
                pos += 1
            if pos >= len(toks):
                raise ValueError(f"{value} has no value")
            kind2, value2 = toks[pos]
            if kind2 == "punct" and value2 == "{":
                pos += 1
                fields.setdefault(value, []).append(message(True))
            elif kind2 == "str":
                pos += 1
                parts = [value2]
                while pos < len(toks) and toks[pos][0] == "str":
                    parts.append(toks[pos][1])
                    pos += 1
                fields.setdefault(value, []).append("".join(parts))
            else:
                pos += 1
                fields.setdefault(value, []).append(value2)
        if closing:
            raise ValueError("missing }")
        return fields

    return message(False)


# ---------------------------------------------------------------------------
# Rules and capability groups on disk
# ---------------------------------------------------------------------------


def _heading(stem):
    words = stem.replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def parse_rule(text, rel):
    """A rule dict from one rule file, or None when it has no frontmatter."""
    m = re.match(r"---\n(.*?)\n---\n?(.*)\Z", text, re.S)
    if not m:
        return None
    meta = {}
    for line in m.group(1).split("\n"):
        key, sep, value = line.partition(":")
        if sep and re.fullmatch(r"[A-Za-z_]+", key):
            meta[key] = value.strip()
    stem = os.path.basename(rel)[: -len(".md")]
    return {
        "id": stem,
        "rel": rel,
        "name": meta.get("name") or stem,
        "heading": _heading(stem),
        "rubric": meta.get("rubric", ""),
        "severity": meta.get("severity", ""),
        "body": m.group(2).strip("\n"),
    }


def _regexes(patterns, where):
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error as e:
            _warn(f"{where}: {pattern!r} is not a regex Python reads ({e}); skipped")
    return compiled


def load_rules(root):
    """{id: rule} for every rule file under `root`."""
    rules = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            if not name.endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8") as f:
                    rule = parse_rule(f.read(), rel)
            except (OSError, UnicodeDecodeError):
                continue
            if rule:
                rules[rule["id"]] = rule
    return rules


def load_groups(root, rules):
    """The capability groups under `root`, each naming rules that exist.

    A group is `{"id", "paths", "contents", "rules"}`: compiled path and content
    regexes (either may be empty, meaning "any") and rule ids. A metadata file
    that does not parse is skipped whole, and says so, rather than guessed at.
    """
    by_rel = {r["rel"]: r["id"] for r in rules.values()}
    groups = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        if METADATA_FILE not in filenames:
            continue
        path = os.path.join(dirpath, METADATA_FILE)
        where = os.path.relpath(path, root).replace(os.sep, "/")
        try:
            with open(path, encoding="utf-8") as f:
                proto = parse_textproto(f.read())
        except (OSError, UnicodeDecodeError, ValueError) as e:
            _warn(f"{where}: {e}; skipped")
            continue
        n = 0
        for review in proto.get("agentic_review", []):
            for group in review.get("capability_groups", []):
                if not isinstance(group, dict):
                    continue
                ids = []
                for listed in group.get("rules", []):
                    rel = (
                        listed[len(RULES_PREFIX) :]
                        if listed.startswith(RULES_PREFIX)
                        else listed
                    )
                    if rel in by_rel:
                        ids.append(by_rel[rel])
                if not ids:
                    continue
                n += 1
                groups.append(
                    {
                        "id": f"{where}#{n}",
                        "paths": _regexes(group.get("path_expression", []), where),
                        "contents": _regexes(group.get("content_patterns", []), where),
                        "rules": ids,
                    }
                )
    return groups


def _rank(severity):
    return SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES)


def _demote_headings(body):
    """`## x` inside a rule body becomes `### x`, outside code fences.

    Detect subagents audit "one entry per ## heading", so a rule must contribute
    exactly one: the one this module writes.
    """
    out = []
    fence = False
    for line in body.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        elif not fence and re.match(r"^#{2,5} ", line):
            line = "#" + line
        out.append(line)
    return "\n".join(out)


class UpstreamRules:
    """The rules under one directory, and which of them a change meets."""

    def __init__(
        self,
        root,
        link_base,
        min_severity=DEFAULT_MIN_SEVERITY,
        severity_map=None,
        triggered_only=False,
        strip_prefixes=(),
    ):
        self.root = root
        self.link_base = link_base.rstrip("/")
        self.min_rank = _rank(min_severity)
        self.severity_map = dict(DEFAULT_SEVERITY_MAP, **(severity_map or {}))
        self.triggered_only = triggered_only
        # `chromium_src/base/x.cc` overrides `base/x.cc`, so both are the file.
        self.strip_prefixes = tuple(strip_prefixes)
        self.rules = load_rules(root)
        self.groups = load_groups(root, self.rules)

    @classmethod
    def from_settings(cls, settings, bot_dir):
        """The rules a profile's `review.upstreamRules` points at, or None.

        None when the key is unset or the directory has not been synced, so a
        review runs on the project's own rules rather than failing.
        """
        if not settings:
            return None
        root = settings["dir"]
        if not os.path.isabs(root):
            root = os.path.join(bot_dir, root)
        if not os.path.isdir(root):
            return None
        found = cls(
            root,
            settings["linkBase"],
            settings.get("minSeverity", DEFAULT_MIN_SEVERITY),
            settings.get("severityMap"),
            settings.get("triggeredOnly", False),
            settings.get("stripPrefixes", ()),
        )
        return found if found.groups else None

    # -- which rules a change meets ---------------------------------------

    def _names(self, path):
        names = [path]
        for prefix in self.strip_prefixes:
            if path.startswith(prefix):
                names.append(path[len(prefix) :])
        return names

    def _group_files(self, group, added):
        files = []
        for path, text in added.items():
            if group["paths"] and not any(
                rx.fullmatch(name)
                for rx in group["paths"]
                for name in self._names(path)
            ):
                continue
            if group["contents"] and not any(
                rx.search(text) for rx in group["contents"]
            ):
                continue
            files.append(path)
        return files

    def scopes(self, added):
        """{rule id: files} for the rules this change meets.

        `added` maps each changed path to the text the change adds to it.
        """
        scopes = {}
        for group in self.groups:
            if self.triggered_only and not group["contents"]:
                continue
            files = self._group_files(group, added)
            if not files:
                continue
            for rule_id in group["rules"]:
                if _rank(self.rules[rule_id]["severity"]) > self.min_rank:
                    continue
                scopes.setdefault(rule_id, set()).update(files)
        return scopes

    def pools(self, added):
        """[{"files", "rules"}]: the rules this change meets, grouped by the files they cover.

        Rules that cover the same files share a pool, so they share the
        subagents that carry the diff of those files.
        """
        by_files = {}
        for rule_id, files in self.scopes(added).items():
            by_files.setdefault(tuple(sorted(files)), []).append(self.rules[rule_id])
        pools = [
            {"files": list(files), "rules": sorted(rules, key=lambda r: r["rel"])}
            for files, rules in by_files.items()
        ]
        pools.sort(key=lambda p: (-len(p["rules"]), p["files"]))
        return pools

    # -- how a rule reads --------------------------------------------------

    def link(self, rule):
        return f"{self.link_base}/{rule['rel']}#{rule['id']}"

    def our_severity(self, rule):
        return self.severity_map.get(rule["severity"], "low")

    def render(self, rule):
        """A rule in the shape detect prompts read: an anchor, one `##` heading, a body."""
        lines = [
            f'<a id="{rule["id"]}"></a>',
            "",
            f"## {rule['heading']}",
            "",
            f"*Chromium rule `{rule['name']}` — rubric: {rule['rubric'] or 'none'}, "
            f"severity: {rule['severity'] or 'none'}, report as `{self.our_severity(rule)}`.*",
            "",
            f"*rule_link: `{self.link(rule)}`*",
            "",
            _demote_headings(rule["body"]),
            "",
        ]
        return "\n".join(lines)

    def chunk(self, rules, index, total, label):
        """A chunk dict shaped like chunk-best-practices.py's, for `rules`."""
        return {
            "doc": f"{label}.md",
            "chunk_index": index,
            "total_chunks": total,
            "rule_count": len(rules),
            "headings": [r["heading"] for r in rules],
            "content": "# Chromium review rules\n\n"
            + "\n".join(self.render(r) for r in rules),
            "upstream": [r["id"] for r in rules],
        }

    # -- links back to rules -------------------------------------------------

    def rule_for_link(self, link):
        """The rule a link from `link()` names, or None."""
        if not link or not link.startswith(self.link_base + "/"):
            return None
        m = _LINK.search(link)
        if not m:
            return None
        rule = self.rules.get(m.group(2))
        if rule and rule["rel"] == link[len(self.link_base) + 1 :].rsplit("#", 1)[0]:
            return rule
        return None

    def owns(self, link):
        """True for any link under this source's base, whether or not it names a rule."""
        return bool(link) and link.startswith(self.link_base + "/")

    def text_for_link(self, link):
        rule = self.rule_for_link(link)
        return self.render(rule) if rule else None

    def heading_for_link(self, link):
        rule = self.rule_for_link(link)
        return rule["heading"] if rule else None

    def index_for(self, rule_ids):
        """[{"url", "heading", "summary"}] for the rules a PR was checked against."""
        index = []
        for rule_id in dict.fromkeys(rule_ids):
            rule = self.rules.get(rule_id)
            if not rule:
                continue
            index.append(
                {
                    "url": self.link(rule),
                    "heading": rule["heading"],
                    "summary": _summary(rule["body"]),
                }
            )
        return index

    def headings(self):
        return [r["heading"] for r in self.rules.values()]


SUMMARY_CHARS = 240


def _summary(body):
    """The first paragraph of a rule, without its bold lead-in, cut at a word."""
    words = []
    for line in body.split("\n"):
        text = line.strip()
        if not text:
            if words:
                break
            continue
        words.append(text)
    flat = re.sub(r"[*_`]", "", " ".join(words))
    flat = re.sub(r"^Rule\.\s*", "", flat)
    if len(flat) <= SUMMARY_CHARS:
        return flat
    return flat[:SUMMARY_CHARS].rsplit(" ", 1)[0] + "..."


def added_text(section):
    """The lines a diff section adds, joined: what `content_patterns` are matched against."""
    return "\n".join(
        line[1:]
        for line in section.split("\n")
        if line.startswith("+") and not line.startswith("+++")
    )
