"""Chromium's review rules as a second rule source for /review-prs.

Covers scripts/lib/upstream_rules.py (the textproto reader, which rules a diff
meets, how a rule reads and links back), the profile key that turns it on, the
places in the review pipeline that resolve an upstream link, and
scripts/sync-upstream-rules.py.
"""

import io
import os
import re
import tarfile

import pytest
from conftest import ROOT_DIR, SCRIPTS_DIR, SKILLS_DIR, _load_module

SKILL_DIR = os.path.join(SKILLS_DIR, "review-prs")
LINK_BASE = "https://chromium.example/rules"

RULE = """---
name: general/{stem}
rubric: {rubric}
severity: {severity}
---

**Rule.** {stem} says do the thing.

## Examples

Bad:

```cpp
## not a heading, it is code
Foo();
```
"""

# A group per file kind, one that also needs the change to add something.
GENERAL_METADATA = r"""# Copyright
agentic_review {
  capability_groups {
    path_expression: "(?:.*/)?[^/]*\\.(?:h|cc)"
    rules: "agents/knowledge_base/rules/general/cpp/use-spans.md"
    rules: "agents/knowledge_base/rules/general/cpp/small-nit.md"
  }
  capability_groups {
    path_expression: "(?:.*/)?[^/]*\\.(?:h|cc)"
    content_patterns: "\\b(?:Unretained|WeakPtr)\\b"
    rules: "agents/knowledge_base/rules/general/cpp/no-unretained-in-posted-tasks.md"
  }
  capability_groups {
    rules: "agents/knowledge_base/rules/general/cpp/every-file.md"
    rules: "agents/knowledge_base/rules/general/cpp/not-on-disk.md"
  }
}
"""

BASE_METADATA = r"""agentic_review {
  capability_groups {
    path_expression: "base/.*"
    rules: "agents/knowledge_base/rules/base/base-only.md"
  }
}
"""


def write_rules(root):
    rules = {
        "general/cpp/use-spans": ("readability", "should_fix"),
        "general/cpp/small-nit": ("readability", "nit"),
        "general/cpp/no-unretained-in-posted-tasks": ("safety", "blocking"),
        "general/cpp/every-file": ("design", "should_fix"),
        "base/base-only": ("correctness", "should_fix"),
    }
    for rel, (rubric, severity) in rules.items():
        path = os.path.join(root, rel + ".md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(
                RULE.format(
                    stem=os.path.basename(rel), rubric=rubric, severity=severity
                )
            )
    for rel, text in (
        ("general/cpp/COMMON_METADATA", GENERAL_METADATA),
        ("base/COMMON_METADATA", BASE_METADATA),
    ):
        with open(os.path.join(root, rel), "w") as f:
            f.write(text)
    return root


@pytest.fixture(scope="module")
def up():
    _load_module("prepare_review", os.path.join(SKILL_DIR, "prepare-review.py"))
    import lib.upstream_rules

    return lib.upstream_rules


@pytest.fixture
def rules_dir(tmp_dir):
    return write_rules(os.path.join(tmp_dir, "rules"))


@pytest.fixture
def rules(up, rules_dir):
    return up.UpstreamRules(rules_dir, LINK_BASE, strip_prefixes=("chromium_src/",))


class TestTextproto:
    def test_nested_repeated_and_commented(self, up):
        proto = up.parse_textproto(
            """
            # a comment
            outer {
              item { name: "a" name: "b" }
              item { name: "c" }  # trailing
              flag: true
            }
            """
        )
        [outer] = proto["outer"]
        assert [i["name"] for i in outer["item"]] == [["a", "b"], ["c"]]
        assert outer["flag"] == ["true"]

    def test_a_regex_keeps_its_backslashes(self, up):
        [value] = up.parse_textproto(r'p: "\\b(?:a|b)\\w*\\b"')["p"]
        assert value == r"\b(?:a|b)\w*\b"

    def test_an_escape_textproto_does_not_define_is_kept_for_the_regex(self, up):
        [value] = up.parse_textproto(r'p: "PRESUBMIT[^/]*\.py"')["p"]
        assert value == r"PRESUBMIT[^/]*\.py"

    def test_quotes_and_adjacent_strings(self, up):
        proto = up.parse_textproto(r'p: "say \"hi\"" p: "ab" "cd"')
        assert proto["p"] == ['say "hi"', "abcd"]

    @pytest.mark.parametrize("text", ["a {", "}", "a: ", "a: @"])
    def test_a_malformed_file_is_an_error_not_a_guess(self, up, text):
        with pytest.raises(ValueError):
            up.parse_textproto(text)


class TestLoading:
    def test_a_rule_is_its_file(self, rules):
        rule = rules.rules["use-spans"]
        assert rule["rel"] == "general/cpp/use-spans.md"
        assert (rule["rubric"], rule["severity"]) == ("readability", "should_fix")
        assert rule["heading"] == "Use spans"
        assert rule["body"].startswith("**Rule.** use-spans")

    def test_a_file_without_frontmatter_is_not_a_rule(self, up):
        assert up.parse_rule("just prose\n", "x.md") is None

    def test_a_group_listing_a_missing_rule_keeps_the_others(self, rules):
        group = next(g for g in rules.groups if "every-file" in g["rules"])
        assert group["rules"] == ["every-file"]
        assert group["paths"] == [] and group["contents"] == []

    def test_a_metadata_file_that_does_not_parse_is_skipped_and_said(
        self, up, rules_dir, capsys
    ):
        with open(os.path.join(rules_dir, "base", "COMMON_METADATA"), "w") as f:
            f.write("agentic_review { capability_groups {")
        loaded = up.UpstreamRules(rules_dir, LINK_BASE)
        assert "base/COMMON_METADATA" in capsys.readouterr().err
        assert "base-only" in loaded.rules
        assert all("base-only" not in g["rules"] for g in loaded.groups)

    def test_a_regex_python_cannot_read_drops_that_pattern_only(
        self, up, rules_dir, capsys
    ):
        with open(os.path.join(rules_dir, "base", "COMMON_METADATA"), "w") as f:
            f.write(
                'agentic_review { capability_groups { path_expression: "(" '
                'path_expression: "base/.*" '
                'rules: "agents/knowledge_base/rules/base/base-only.md" } }'
            )
        loaded = up.UpstreamRules(rules_dir, LINK_BASE)
        assert "is not a regex" in capsys.readouterr().err
        [group] = [g for g in loaded.groups if g["rules"] == ["base-only"]]
        assert len(group["paths"]) == 1


class TestWhichRulesAChangeMeets:
    def scopes(self, rules, added):
        return {k: sorted(v) for k, v in rules.scopes(added).items()}

    def test_a_path_must_match_whole(self, rules):
        got = self.scopes(rules, {"a/b.cc": "", "a/b.cc.orig": "", "base/x.cc": ""})
        assert got["use-spans"] == ["a/b.cc", "base/x.cc"]
        assert got["base-only"] == ["base/x.cc"]

    def test_a_group_with_no_path_applies_to_every_file(self, rules):
        assert self.scopes(rules, {"README.md": ""})["every-file"] == ["README.md"]

    def test_content_patterns_read_only_what_the_change_adds(self, rules):
        got = self.scopes(
            rules, {"a.cc": "base::Unretained(this)", "b.cc": "nothing here"}
        )
        assert got["no-unretained-in-posted-tasks"] == ["a.cc"]
        assert (
            self.scopes(rules, {"b.cc": "x"}).get("no-unretained-in-posted-tasks")
            is None
        )

    def test_an_override_is_the_file_it_overrides(self, rules):
        got = self.scopes(rules, {"chromium_src/base/x.cc": ""})
        assert got["base-only"] == ["chromium_src/base/x.cc"]
        assert "base-only" not in self.scopes(rules, {"base_other/x.cc": ""})

    def test_rules_below_the_severity_floor_are_left_out(self, up, rules_dir):
        got = up.UpstreamRules(rules_dir, LINK_BASE).scopes({"a.cc": "Unretained"})
        assert "small-nit" not in got
        assert "no-unretained-in-posted-tasks" in got
        nits = up.UpstreamRules(rules_dir, LINK_BASE, min_severity="nit")
        assert "small-nit" in nits.scopes({"a.cc": ""})
        blocking = up.UpstreamRules(rules_dir, LINK_BASE, min_severity="blocking")
        assert set(blocking.scopes({"a.cc": "Unretained"})) == {
            "no-unretained-in-posted-tasks"
        }

    def test_triggered_only_skips_the_groups_a_path_alone_meets(self, up, rules_dir):
        rules = up.UpstreamRules(rules_dir, LINK_BASE, triggered_only=True)
        assert rules.scopes({"a.cc": "nothing"}) == {}
        assert set(rules.scopes({"a.cc": "WeakPtr"})) == {
            "no-unretained-in-posted-tasks"
        }

    def test_rules_covering_the_same_files_share_a_pool(self, rules):
        pools = rules.pools({"a.cc": "Unretained", "b.md": ""})
        by_files = {tuple(p["files"]): [r["id"] for r in p["rules"]] for p in pools}
        assert by_files[("a.cc",)] == [
            "no-unretained-in-posted-tasks",
            "use-spans",
        ]
        assert by_files[("a.cc", "b.md")] == ["every-file"]

    def test_added_text_is_the_plus_lines(self, up):
        section = "--- a/x\n+++ b/x\n@@ -1 +1,2 @@\n kept\n-gone\n+added\n+more\n"
        assert up.added_text(section) == "added\nmore"


class TestHowARuleReads:
    def test_it_has_one_heading_and_one_anchor(self, rules):
        text = rules.render(rules.rules["use-spans"])
        outside_code = re.sub(r"```.*?```", "", text, flags=re.S)
        assert re.findall(r"^## .*$", outside_code, flags=re.M) == ["## Use spans"]
        assert '<a id="use-spans"></a>' in text
        assert "### Examples" in text
        assert "## not a heading, it is code" in text  # code is left alone

    def test_it_carries_its_link_and_the_severity_to_report(self, rules):
        text = rules.render(rules.rules["no-unretained-in-posted-tasks"])
        assert (
            f"{LINK_BASE}/general/cpp/no-unretained-in-posted-tasks.md"
            "#no-unretained-in-posted-tasks"
        ) in text
        assert "report as `medium`" in text  # blocking, by default
        assert "report as `low`" in rules.render(rules.rules["use-spans"])

    def test_the_severity_map_can_be_changed(self, up, rules_dir):
        rules = up.UpstreamRules(
            rules_dir, LINK_BASE, severity_map={"should_fix": "high"}
        )
        assert rules.our_severity(rules.rules["use-spans"]) == "high"
        assert rules.our_severity(rules.rules["small-nit"]) == "low"

    def test_a_chunk_is_shaped_like_the_chunkers(self, rules):
        chunk = rules.chunk([rules.rules["use-spans"]], 0, 1, "chromium-rules-1")
        assert chunk["doc"] == "chromium-rules-1.md"
        assert (chunk["chunk_index"], chunk["total_chunks"], chunk["rule_count"]) == (
            0,
            1,
            1,
        )
        assert chunk["headings"] == ["Use spans"]
        assert chunk["upstream"] == ["use-spans"]


class TestLinksBack:
    def test_a_link_round_trips(self, rules):
        rule = rules.rules["use-spans"]
        assert rules.rule_for_link(rules.link(rule)) is rule
        assert rules.heading_for_link(rules.link(rule)) == "Use spans"
        assert "use-spans" in rules.text_for_link(rules.link(rule))

    @pytest.mark.parametrize(
        "link",
        [
            f"{LINK_BASE}/general/cpp/nope.md#nope",
            f"{LINK_BASE}/general/cpp/use-spans.md#nope",
            f"{LINK_BASE}/base/use-spans.md#use-spans",  # right id, wrong path
            f"{LINK_BASE}/general/cpp/use-spans.md",
            "https://example.com/general/cpp/use-spans.md#use-spans",
            "",
            None,
        ],
    )
    def test_a_link_naming_no_rule_is_none(self, rules, link):
        assert rules.rule_for_link(link) is None

    def test_only_links_under_the_base_are_ours(self, rules):
        assert rules.owns(f"{LINK_BASE}/anything")
        assert not rules.owns("https://example.com/x.md#y")
        assert not rules.owns(None)

    def test_the_pipeline_judges_our_links_and_leaves_others_be(self, up, rules):
        import lib.rule_index as ri

        good = rules.link(rules.rules["use-spans"])
        made_up = f"{LINK_BASE}/general/cpp/made-up.md#made-up"
        assert ri.names_no_rule(good, "/nowhere", rules) is False
        assert ri.names_no_rule(made_up, "/nowhere", rules) is True
        assert ri.heading_of(good, "/nowhere", rules) == "Use spans"
        assert ri.heading_of(made_up, "/nowhere", rules) is None
        # With no upstream, the same link is just a link into a doc we do not hold.
        assert ri.names_no_rule(made_up, "/nowhere") is False

    def test_an_index_names_the_rules_a_pr_was_checked_against(self, rules):
        index = rules.index_for(["use-spans", "use-spans", "nope"])
        assert [r["heading"] for r in index] == ["Use spans"]
        assert index[0]["url"] == rules.link(rules.rules["use-spans"])
        assert index[0]["summary"] == "use-spans says do the thing."


class TestProfile:
    def test_a_malformed_key_means_no_rules_not_a_broken_review(self, up):
        import lib.load_config as lc

        assert lc.review_upstream_rules({}) is None
        assert lc.review_upstream_rules({"review": {"upstreamRules": "x"}}) is None
        assert (
            lc.review_upstream_rules({"review": {"upstreamRules": {"dir": "d"}}})
            is None
        )
        got = lc.review_upstream_rules(
            {
                "review": {
                    "upstreamRules": {
                        "dir": " d ",
                        "linkBase": "https://x",
                        "minSeverity": 3,
                        "triggeredOnly": "yes",
                        "severityMap": {"blocking": "high", "nit": "urgent"},
                        "stripPrefixes": ["a/", 4, ""],
                    }
                }
            }
        )
        assert got == {
            "dir": "d",
            "linkBase": "https://x",
            "severityMap": {"blocking": "high"},
            "stripPrefixes": ["a/"],
        }

    def test_only_brave_core_uses_them(self, up):
        import lib.load_config as lc

        for name in ("bravebot", "brave-dev-loop", "default"):
            profile = lc.load_profile({"project": {"profile": name}}, ROOT_DIR)
            assert lc.review_upstream_rules(profile) is None, name
        profile = lc.load_profile({"project": {"profile": "brave-core"}}, ROOT_DIR)
        settings = lc.review_upstream_rules(profile)
        assert settings["linkBase"].startswith("https://chromium.googlesource.com/")
        assert settings["source"].startswith("https://")
        assert settings["triggeredOnly"] is True

    def test_nothing_is_loaded_before_a_sync(self, up, tmp_dir):
        settings = {"dir": "missing", "linkBase": LINK_BASE}
        assert up.UpstreamRules.from_settings(settings, tmp_dir) is None
        assert up.UpstreamRules.from_settings(None, tmp_dir) is None

    def test_a_relative_dir_is_the_bot_directorys(self, up, tmp_dir):
        write_rules(os.path.join(tmp_dir, "synced"))
        found = up.UpstreamRules.from_settings(
            {"dir": "synced", "linkBase": LINK_BASE, "triggeredOnly": True}, tmp_dir
        )
        assert found is not None and found.triggered_only


@pytest.fixture(scope="module")
def prep():
    return _load_module("prepare_review", os.path.join(SKILL_DIR, "prepare-review.py"))


class TestPipeline:
    @pytest.fixture
    def chunk(self, rules):
        return rules.chunk([rules.rules["use-spans"]], 0, 1, "chromium-rules-1")

    def test_the_detect_prompt_asks_for_the_rules_own_link(self, prep, chunk):
        prompt = prep.build_detect_prompt(
            {"number": 7, "title": "t"}, chunk, "diff", [], {}, "/c.json", "c"
        )
        assert "Chromium's own review rules" in prompt
        assert "`rule_link` line under it" in prompt
        assert "<doc>.md#<ID>" not in prompt  # not our docs' link shape

    def test_a_project_doc_chunk_keeps_its_own_requirement(self, prep):
        chunk = {
            "doc": "x.md",
            "chunk_index": 0,
            "total_chunks": 1,
            "rule_count": 1,
            "headings": ["h"],
            "content": "c",
        }
        prompt = prep.build_detect_prompt(
            {"number": 7, "title": "t"}, chunk, "diff", [], {}, "/c.json", "c"
        )
        assert "Chromium's own review rules" not in prompt
        assert "Do NOT invent IDs" in prompt

    def test_the_prompt_entry_records_which_upstream_rules_it_carried(
        self, prep, chunk
    ):
        entry = prep._prompt_entry("rules", "id", "/p", "/r", "prompt", chunk)
        assert entry["upstream"] == ["use-spans"]
        plain = prep._prompt_entry(
            "rules", "id", "/p", "/r", "prompt", {**chunk, "upstream": []}
        )
        assert "upstream" not in plain

    def test_the_prepare_script_loads_the_rules_once_and_only_when_asked(
        self, prep, tmp_dir, monkeypatch
    ):
        write_rules(os.path.join(tmp_dir, "synced"))
        monkeypatch.setattr(prep, "_BOT_DIR", tmp_dir)
        monkeypatch.setattr(prep, "_UPSTREAM", [])
        monkeypatch.setattr(prep, "REVIEW_UPSTREAM_RULES", None)
        assert prep.upstream_rules() is None
        monkeypatch.setattr(prep, "_UPSTREAM", [])
        monkeypatch.setattr(
            prep,
            "REVIEW_UPSTREAM_RULES",
            {"dir": "synced", "linkBase": LINK_BASE},
        )
        first = prep.upstream_rules()
        assert first is not None and prep.upstream_rules() is first

    def test_a_synced_link_is_a_rule_to_the_selector_and_a_made_up_one_is_not(
        self, prep, rules, monkeypatch
    ):
        sel = _load_module(
            "select_candidates", os.path.join(SKILL_DIR, "select-candidates.py")
        )
        monkeypatch.setattr(sel._prep, "_UPSTREAM", [rules])
        good = rules.link(rules.rules["use-spans"])
        assert "use-spans" in sel.rule_text(good, "/nowhere")
        assert (
            sel.rule_text(f"{LINK_BASE}/general/cpp/made-up.md#made-up", "/nowhere")
            is None
        )

    def test_the_validators_index_lists_upstream_rules_the_pr_met(
        self, prep, rules, monkeypatch
    ):
        sel = _load_module(
            "select_candidates", os.path.join(SKILL_DIR, "select-candidates.py")
        )
        monkeypatch.setattr(sel._prep, "_UPSTREAM", [rules])
        pr = {
            "subagent_prompts": [
                {
                    "kind": "rules",
                    "doc": "chromium-rules-1.md",
                    "upstream": ["use-spans"],
                },
                {"kind": "correctness", "chunk_id": "correctness"},
            ]
        }
        assert [r["heading"] for r in sel.rule_index(pr, "/nowhere")] == ["Use spans"]

    def test_collect_drops_a_made_up_upstream_link_and_keeps_a_real_one(self, rules):
        collect = _load_module(
            "collect_results", os.path.join(SKILL_DIR, "collect-results.py")
        )
        made_up = f"{LINK_BASE}/general/cpp/made-up.md#made-up"
        good = rules.link(rules.rules["use-spans"])
        found = collect.settle_rule_links(
            [
                {"file": "a.cc", "line": 1, "rule_link": made_up, "draft_comment": "x"},
                {"file": "b.cc", "line": 2, "rule_link": good, "draft_comment": "y"},
                {"file": "c.cc", "line": 3, "cited_rule_link": good},
                {"file": "d.cc", "line": 4, "cited_rule_link": made_up},
            ],
            "/nowhere",
            rules,
        )
        assert "rule_link" not in found[0]
        assert found[1]["rule_link"] == good
        assert (found[2]["cited_rule_link"], found[2]["cited_rule"]) == (
            good,
            "Use spans",
        )
        assert "cited_rule_link" not in found[3]


def make_tar(path, members):
    """members: [(name, bytes | None for a symlink to /etc/passwd)]."""
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))


def good_members():
    rule = RULE.format(
        stem="use-spans", rubric="design", severity="should_fix"
    ).encode()
    meta = (
        b"agentic_review { capability_groups { rules: "
        b'"agents/knowledge_base/rules/general/use-spans.md" } }'
    )
    return [("general/use-spans.md", rule), ("general/COMMON_METADATA", meta)]


@pytest.fixture(scope="module")
def sync():
    return _load_module(
        "sync_upstream_rules", os.path.join(SCRIPTS_DIR, "sync-upstream-rules.py")
    )


class TestSync:
    def test_it_installs_the_rules_and_says_when(self, sync, tmp_dir):
        archive = os.path.join(tmp_dir, "r.tar.gz")
        make_tar(archive, good_members())
        dest = os.path.join(tmp_dir, "out", "chromium")
        summary = sync.install(archive, dest, "https://example/r.tar.gz")
        assert summary["rules"] == 1 and summary["groups"] == 1
        assert summary["severities"]["should_fix"] == 1
        assert os.path.isfile(os.path.join(dest, "general", "use-spans.md"))
        with open(os.path.join(dest, "SYNCED")) as f:
            assert "source: https://example/r.tar.gz" in f.read()
        assert os.listdir(os.path.dirname(dest)) == ["chromium"]  # no staging left

    def test_it_keeps_only_rules_and_metadata_inside_the_directory(self, sync, tmp_dir):
        archive = os.path.join(tmp_dir, "r.tar.gz")
        make_tar(
            archive,
            good_members()
            + [
                ("../escape.md", b"x"),
                ("/abs.md", b"x"),
                ("general/run.sh", b"#!/bin/sh"),
                ("general/link.md", None),
            ],
        )
        dest = os.path.join(tmp_dir, "out", "chromium")
        sync.install(archive, dest)
        found = sorted(
            os.path.relpath(os.path.join(d, n), dest)
            for d, _, names in os.walk(dest)
            for n in names
        )
        assert found == ["SYNCED", "general/COMMON_METADATA", "general/use-spans.md"]
        assert not os.path.exists(os.path.join(tmp_dir, "out", "escape.md"))

    def test_a_download_that_holds_no_rules_leaves_the_old_ones(self, sync, tmp_dir):
        good = os.path.join(tmp_dir, "good.tar.gz")
        make_tar(good, good_members())
        dest = os.path.join(tmp_dir, "out", "chromium")
        sync.install(good, dest)
        empty = os.path.join(tmp_dir, "empty.tar.gz")
        make_tar(empty, [("readme.txt", b"not rules")])
        with pytest.raises(ValueError):
            sync.install(empty, dest)
        assert os.path.isfile(os.path.join(dest, "general", "use-spans.md"))
        assert os.listdir(os.path.dirname(dest)) == ["chromium"]

    def test_a_newer_download_replaces_the_old_rules_whole(self, sync, tmp_dir):
        first = os.path.join(tmp_dir, "a.tar.gz")
        make_tar(first, good_members())
        dest = os.path.join(tmp_dir, "out", "chromium")
        sync.install(first, dest)
        second = os.path.join(tmp_dir, "b.tar.gz")
        rule = RULE.format(stem="other", rubric="design", severity="nit").encode()
        meta = (
            b"agentic_review { capability_groups { rules: "
            b'"agents/knowledge_base/rules/general/other.md" } }'
        )
        make_tar(
            second, [("general/other.md", rule), ("general/COMMON_METADATA", meta)]
        )
        sync.install(second, dest)
        assert not os.path.exists(os.path.join(dest, "general", "use-spans.md"))
        assert os.path.isfile(os.path.join(dest, "general", "other.md"))

    def test_only_https_is_downloaded(self, sync, tmp_dir):
        for url in ("http://example.com/r.tar.gz", "file:///etc/passwd", "ftp://x/y"):
            with pytest.raises(ValueError):
                sync.download(url, os.path.join(tmp_dir, "x"))
