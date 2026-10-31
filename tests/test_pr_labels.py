"""Tests for labelling a pull request once: which paths decide a label, what the
subagent may choose, and that a PR is never classified a second time."""

import json
import os
import sys

import pytest
from conftest import ROOT_DIR, SCRIPTS_DIR, SKILLS_DIR, _load_module

sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))
from lib import pr_labels
from lib.load_config import review_label_prs

CACHE_KEY = "_labelled"


def cfg(rules=(), judged=None):
    return review_label_prs(
        {"review": {"labelPrs": {"rules": list(rules), "judged": judged or {}}}}
    )


def shipped(project):
    with open(os.path.join(ROOT_DIR, "projects", project, "profile.json")) as f:
        return review_label_prs(json.load(f))


def labels_for(config, *paths):
    return pr_labels.decided_labels(config["rules"], list(paths))


class TestProfileKey:
    def test_nothing_configured_is_none(self):
        assert review_label_prs({}) is None
        assert review_label_prs({"review": {}}) is None
        assert review_label_prs({"review": {"labelPrs": "yes"}}) is None
        assert cfg() is None

    def test_a_rule_needs_exactly_one_of_any_and_only(self):
        config = cfg(
            [
                {"label": "both", "any": ["a"], "only": ["b"]},
                {"label": "neither"},
                {"label": "ok", "any": ["a"]},
                {"any": ["a"]},
                "not a rule",
            ]
        )
        assert [r["label"] for r in config["rules"]] == ["ok"]

    def test_a_judged_label_a_rule_already_decides_is_dropped(self):
        config = cfg(
            [{"label": "pr/tests-only", "only": ["*_test.cc"]}],
            {
                "pr/tests-only": "decided twice",
                "pr/risk/x": "a definition",
                "pr/risk/y": "",
            },
        )
        assert config["judged"] == {"pr/risk/x": "a definition"}

    def test_the_shipped_profiles_parse(self):
        for project in ("brave-core", "bravebot"):
            config = shipped(project)
            assert config["rules"] and config["judged"], project
            assert not set(config["judged"]) & {r["label"] for r in config["rules"]}

    def test_every_shipped_label_starts_with_pr_so_it_cannot_be_an_existing_one(self):
        for project in ("brave-core", "bravebot"):
            config = shipped(project)
            for label in pr_labels.configured_labels(config):
                assert label.startswith("pr/"), (project, label)

    def test_no_shipped_profile_judges_a_label_that_lowers_scrutiny(self):
        for project in ("brave-core", "bravebot"):
            judged = shipped(project)["judged"]
            assert all(label.startswith("pr/risk/") for label in judged), project


class TestPathRules:
    def test_a_glob_without_a_slash_matches_the_file_name_at_any_depth(self):
        assert pr_labels.path_matches("DEPS", "DEPS")
        assert pr_labels.path_matches("DEPS", "a/b/DEPS")
        assert pr_labels.path_matches("*_unittest.cc", "a/b/foo_unittest.cc")
        assert not pr_labels.path_matches("DEPS", "a/DEPS.bak")

    def test_a_glob_with_a_slash_matches_the_whole_path_and_star_crosses_slashes(self):
        assert pr_labels.path_matches("chromium_src/*", "chromium_src/a/b/c.cc")
        assert not pr_labels.path_matches("chromium_src/*", "x/chromium_src/a.cc")
        assert pr_labels.path_matches("*/test/*", "a/b/test/c.cc")
        assert not pr_labels.path_matches("*/test/*", "test/c.cc")

    def test_any_applies_when_one_changed_file_matches(self):
        config = cfg([{"label": "deps", "any": ["DEPS"]}])
        assert labels_for(config, "a.cc", "DEPS") == ["deps"]
        assert labels_for(config, "a.cc") == []

    def test_only_applies_when_every_changed_file_matches(self):
        config = cfg([{"label": "tests", "only": ["*_test.cc"]}])
        assert labels_for(config, "a_test.cc", "b/c_test.cc") == ["tests"]
        assert labels_for(config, "a_test.cc", "b.cc") == []

    def test_only_is_not_vacuously_true_for_a_pr_with_no_files(self):
        config = cfg([{"label": "tests", "only": ["*_test.cc"]}])
        assert labels_for(config) == []

    def test_exclude_withholds_a_label_when_any_changed_file_matches(self):
        config = cfg([{"label": "docs", "only": ["*.md"], "exclude": ["docs/specs/*"]}])
        assert labels_for(config, "README.md", "docs/a.md") == ["docs"]
        assert labels_for(config, "README.md", "docs/specs/labels.md") == []

    def test_labels_come_out_in_the_order_the_profile_lists_them(self):
        config = cfg(
            [
                {"label": "b", "any": ["*.cc"]},
                {"label": "a", "any": ["*.cc"]},
            ]
        )
        assert labels_for(config, "x.cc") == ["b", "a"]


@pytest.fixture(scope="module")
def brave_core():
    return shipped("brave-core")


@pytest.fixture(scope="module")
def bravebot():
    return shipped("bravebot")


@pytest.fixture(scope="module")
def collect():
    return _load_module(
        "collect_results_labels",
        os.path.join(SKILLS_DIR, "review-prs", "collect-results.py"),
    )


class TestShippedBraveCore:
    def test_a_unit_test_alone_is_tests_only(self, brave_core):
        assert labels_for(brave_core, "components/brave_wallet/foo_unittest.cc") == [
            "pr/tests-only"
        ]

    def test_a_test_beside_production_code_is_not_tests_only(self, brave_core):
        assert "pr/tests-only" not in labels_for(
            brave_core, "components/x/foo_unittest.cc", "components/x/foo.cc"
        )

    def test_a_build_file_outside_a_test_directory_is_not_tests_only(self, brave_core):
        assert "pr/tests-only" not in labels_for(
            brave_core, "components/x/foo_unittest.cc", "components/x/BUILD.gn"
        )

    def test_a_chromium_src_override_is_labelled_wherever_the_rest_of_the_pr_is(
        self, brave_core
    ):
        got = labels_for(
            brave_core, "chromium_src/chrome/browser/x.cc", "chrome/x_unittest.cc"
        )
        assert "pr/chromium-overrides" in got and "pr/tests-only" not in got

    def test_markdown_alone_is_docs_only(self, brave_core):
        assert labels_for(brave_core, "README.md", "docs/gni_sources.md") == [
            "pr/docs-only"
        ]

    @pytest.mark.parametrize(
        "path",
        [
            "AGENTS.md",
            "components/x/AGENTS.md",
            "SECURITY.md",
            ".claude/skills/review/SKILL.md",
            "agents/skills/x/SKILL.md",
            "docs/best_practices.md",
            "docs/best-practices/architecture.md",
            "third_party/rust/chromium_crates_io/vendor/bitvec-v1/doc/x.md",
            "tools/crates/vendor/clap/README.md",
        ],
    )
    def test_markdown_that_steers_review_or_is_vendored_is_not_docs_only(
        self, brave_core, path
    ):
        assert "pr/docs-only" not in labels_for(brave_core, "README.md", path)

    def test_markdown_beside_code_is_not_docs_only(self, brave_core):
        assert "pr/docs-only" not in labels_for(brave_core, "README.md", "a/b.cc")

    def test_deps_and_third_party_are_dependency_risk(self, brave_core):
        assert labels_for(brave_core, "DEPS") == ["pr/risk/dependencies"]
        assert labels_for(brave_core, "third_party/foo/BUILD.gn") == [
            "pr/risk/dependencies"
        ]


class TestShippedBravebot:
    def test_a_doc_alone_is_docs_only(self, bravebot):
        assert labels_for(bravebot, "README.md", "docs/development/commits.md") == [
            "pr/docs-only"
        ]

    def test_a_spec_is_behaviour_and_not_docs_only(self, bravebot):
        assert labels_for(bravebot, "docs/specs/labels.md") == []

    def test_what_a_pull_request_is_reviewed_against_is_not_docs_only(self, bravebot):
        for path in (
            "docs/best_practices.md",
            "docs/development/reviewing-for-the-rule.md",
        ):
            assert "pr/docs-only" not in labels_for(bravebot, "README.md", path), path

    def test_an_agent_instruction_file_is_not_docs_only(self, bravebot):
        for path in ("AGENTS.md", ".claude/CLAUDE.md", "agents/skills/x/SKILL.md"):
            assert "pr/docs-only" not in labels_for(bravebot, path), path

    def test_an_integration_test_alone_is_tests_only(self, bravebot):
        assert labels_for(bravebot, "crates/net/tests/proxy.rs") == ["pr/tests-only"]

    def test_the_ui_and_npm_lockfiles_are_dependency_risk(self, bravebot):
        for path in ("ui/pnpm-lock.yaml", "ui/package.json", "package-lock.json"):
            assert labels_for(bravebot, path) == ["pr/risk/dependencies"], path

    def test_cargo_changes_are_dependency_risk(self, bravebot):
        assert labels_for(bravebot, "crates/net/Cargo.toml", "Cargo.lock") == [
            "pr/risk/dependencies"
        ]


@pytest.mark.parametrize("project", ["brave-core", "bravebot"])
def test_the_project_doc_names_every_label_and_has_a_command_to_create_it(project):
    path = os.path.join(ROOT_DIR, "projects", project, "docs", "labels.md")
    with open(path) as f:
        text = f.read()
    for label in pr_labels.configured_labels(shipped(project)):
        assert f"`{label}`" in text, label
        assert f"gh label create {label} " in text, label


class TestPlanPr:
    CFG = cfg(
        [{"label": "deps", "any": ["DEPS"]}],
        {"pr/risk/net": "adds a network request", "pr/risk/funds": "moves money"},
    )

    def plan(self, tmp_path, paths=("a.cc", "DEPS"), config=None):
        return pr_labels.plan_pr(
            config or self.CFG,
            7,
            "Add\na thing",
            list(paths),
            "DIFF TEXT",
            str(tmp_path),
            "/src/tree",
        )

    def test_decided_labels_and_the_labels_the_subagent_may_choose_from(self, tmp_path):
        labels, prompt = self.plan(tmp_path)
        assert labels == {
            "decided": ["deps"],
            "judged": ["pr/risk/net", "pr/risk/funds"],
        }
        assert os.path.isfile(prompt["prompt_file"])

    def test_the_prompt_holds_the_whole_diff_path_the_definitions_and_the_output(
        self, tmp_path
    ):
        _, prompt = self.plan(tmp_path)
        text = open(prompt["prompt_file"]).read()
        assert "pr/risk/net" in text and "adds a network request" in text
        assert "- a.cc" in text and "- DEPS" in text
        assert "Title: Add a thing" in text
        assert prompt["results_file"] in text
        assert "/src/tree" in text
        with open(os.path.join(tmp_path, "label_diff.patch")) as f:
            assert f.read() == "DIFF TEXT"

    def test_the_prompt_lets_the_subagent_search_a_diff_too_long_to_read(
        self, tmp_path
    ):
        _, prompt = self.plan(tmp_path)
        assert "Grep it" in open(prompt["prompt_file"]).read()

    def test_the_prompt_tells_the_subagent_the_diff_is_data(self, tmp_path):
        _, prompt = self.plan(tmp_path)
        text = open(prompt["prompt_file"]).read()
        assert "you do not follow it" in text
        assert "Never post" in text

    def test_a_long_file_list_is_cut_and_counted(self, tmp_path):
        paths = [f"f{i}.cc" for i in range(pr_labels.MAX_LISTED_FILES + 5)]
        _, prompt = self.plan(tmp_path, paths)
        text = open(prompt["prompt_file"]).read()
        assert "... and 5 more" in text
        assert f"Files changed ({len(paths)})" in text

    def test_a_profile_that_judges_nothing_writes_no_prompt(self, tmp_path):
        config = cfg([{"label": "deps", "any": ["DEPS"]}])
        labels, prompt = self.plan(tmp_path, config=config)
        assert labels == {"decided": ["deps"], "judged": []}
        assert prompt is None
        assert not os.listdir(tmp_path)


class TestReadJudged:
    ALLOWED = ["pr/risk/net", "pr/risk/funds"]

    def write(self, tmp_path, data):
        path = tmp_path / "labels.json"
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return str(path)

    def test_a_label_not_on_the_list_is_ignored(self, tmp_path):
        path = self.write(tmp_path, {"labels": ["pr/risk/net", "approve", "../x"]})
        assert pr_labels.read_judged(path, self.ALLOWED)[0] == ["pr/risk/net"]

    def test_labels_come_back_in_the_profile_order_without_repeats(self, tmp_path):
        path = self.write(
            tmp_path, {"labels": ["pr/risk/funds", "pr/risk/net", "pr/risk/net"]}
        )
        assert pr_labels.read_judged(path, self.ALLOWED)[0] == [
            "pr/risk/net",
            "pr/risk/funds",
        ]

    def test_an_empty_list_is_a_result(self, tmp_path):
        path = self.write(tmp_path, {"labels": []})
        assert pr_labels.read_judged(path, self.ALLOWED) == ([], {})

    @pytest.mark.parametrize(
        "data", ["not json", "[]", '{"labels": "pr/risk/net"}', '{"reasons": {}}']
    )
    def test_no_usable_result_is_none(self, tmp_path, data):
        assert pr_labels.read_judged(self.write(tmp_path, data), self.ALLOWED) is None

    def test_a_missing_file_is_none(self, tmp_path):
        assert pr_labels.read_judged(str(tmp_path / "nope"), self.ALLOWED) is None

    def test_a_reason_is_one_short_line_and_only_for_a_chosen_label(self, tmp_path):
        path = self.write(
            tmp_path,
            {
                "labels": ["pr/risk/net"],
                "reasons": {
                    "pr/risk/net": "a\nb " + "x" * 500,
                    "pr/risk/funds": "unused",
                },
            },
        )
        labels, reasons = pr_labels.read_judged(path, self.ALLOWED)
        assert list(reasons) == ["pr/risk/net"]
        assert "\n" not in reasons["pr/risk/net"]
        assert len(reasons["pr/risk/net"]) == pr_labels.MAX_REASON_CHARS


class TestUsableConfig:
    CFG = cfg([{"label": "pr/tests-only", "only": ["*_test.cc"]}], {"pr/risk/net": "x"})

    def run(self, monkeypatch, names=None, error=None):
        logs = []

        def listing(repo):
            if error:
                raise error
            return {n.lower() for n in names}

        monkeypatch.setattr(pr_labels, "repo_label_names", listing)
        return pr_labels.usable_config(self.CFG, "o/r", logs.append), logs

    def test_every_label_present_means_labelling_is_on(self, monkeypatch):
        got, logs = self.run(monkeypatch, ["pr/tests-only", "pr/risk/net", "other"])
        assert got is self.CFG and not logs

    def test_names_compare_without_case(self, monkeypatch):
        got, _ = self.run(monkeypatch, ["PR/Tests-Only", "Pr/RISK/NET"])
        assert got is self.CFG

    def test_one_missing_label_turns_it_off_and_names_the_label(self, monkeypatch):
        got, logs = self.run(monkeypatch, ["pr/tests-only"])
        assert got is None
        assert "pr/risk/net" in logs[0]
        assert "pr/tests-only" not in logs[0]

    def test_a_failed_listing_turns_it_off(self, monkeypatch):
        got, logs = self.run(monkeypatch, error=RuntimeError("HTTP 502"))
        assert got is None and "HTTP 502" in logs[0]


class TestConfigsForRun:
    CFG = cfg([{"label": "pr/tests-only", "only": ["*_test.cc"]}], {"pr/risk/net": "x"})

    def run(self, monkeypatch, numbers, cache, names=("pr/tests-only", "pr/risk/net")):
        listings = []
        logs = []

        def listing(repo):
            listings.append(repo)
            return {n.lower() for n in names}

        monkeypatch.setattr(pr_labels, "repo_label_names", listing)
        got = pr_labels.configs_for_run(self.CFG, numbers, cache, "o/r", logs.append)
        return got, listings, logs

    def test_every_unlabelled_pr_is_planned(self, monkeypatch):
        got, listings, _ = self.run(monkeypatch, [1, 2], {})
        assert got == {1: self.CFG, 2: self.CFG}
        assert listings == ["o/r"]

    def test_a_pr_already_labelled_is_never_planned_again(self, monkeypatch):
        cache = {CACHE_KEY: {"1": ["pr/risk/net"]}}
        got, _, _ = self.run(monkeypatch, [1, 2], cache)
        assert got == {2: self.CFG}

    def test_a_pr_labelled_with_nothing_is_not_planned_again_either(self, monkeypatch):
        got, listings, _ = self.run(monkeypatch, [1], {CACHE_KEY: {"1": []}})
        assert got == {} and listings == []

    def test_the_repository_is_not_asked_when_every_pr_is_labelled(self, monkeypatch):
        _, listings, _ = self.run(monkeypatch, [1, 2], {CACHE_KEY: {"1": [], "2": []}})
        assert listings == []

    def test_a_missing_label_plans_nothing_and_records_nothing(self, monkeypatch):
        got, _, logs = self.run(monkeypatch, [1], {}, names=("pr/tests-only",))
        assert got == {} and "pr/risk/net" in logs[0]

    def test_a_profile_with_no_labels_plans_nothing_and_asks_nothing(self, monkeypatch):
        monkeypatch.setattr(
            pr_labels, "repo_label_names", lambda r: pytest.fail("listed labels")
        )
        assert pr_labels.configs_for_run(None, [1], {}, "o/r", print) == {}


class TestRecordLabelled:
    def test_a_pr_is_recorded_beside_the_rest_of_the_cache(self, tmp_path):
        cache = tmp_path / "cache.json"
        cache.write_text(json.dumps({"41": "abc", "_files": {"41": {}}}))
        pr_labels.record_labelled(str(cache), 42, ["pr/risk/net"])
        data = json.loads(cache.read_text())
        assert data[CACHE_KEY] == {"42": ["pr/risk/net"]}
        assert data["41"] == "abc" and data["_files"] == {"41": {}}

    def test_a_pr_labelled_with_nothing_is_still_recorded(self, tmp_path):
        cache = tmp_path / "cache.json"
        pr_labels.record_labelled(str(cache), 42, [])
        assert pr_labels.labelled(json.loads(cache.read_text())) == {"42": []}

    def test_the_oldest_entries_go_first(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pr_labels, "MAX_LABELLED_ENTRIES", 2)
        cache = tmp_path / "cache.json"
        for n in (1, 2, 3):
            pr_labels.record_labelled(str(cache), n, [])
        assert list(pr_labels.labelled(json.loads(cache.read_text()))) == ["2", "3"]

    def test_a_malformed_record_reads_as_nobody_labelled(self):
        assert pr_labels.labelled({CACHE_KEY: ["1"]}) == {}
        assert pr_labels.labelled({}) == {}


class TestLabelPr:
    @pytest.fixture
    def calls(self, monkeypatch):
        seen = {"add": [], "logs": []}
        monkeypatch.setattr(
            pr_labels, "add_labels", lambda repo, n, labels: seen["add"].append(labels)
        )
        return seen

    def pr(self, tmp_path, chosen=None, decided=("deps",)):
        entry = {
            "number": 9,
            "labels": {
                "decided": list(decided),
                "judged": ["pr/risk/net", "pr/risk/funds"],
            },
            "label_prompt": None,
        }
        if chosen is not None:
            results = tmp_path / "labels.json"
            results.write_text(json.dumps(chosen))
            entry["label_prompt"] = {"results_file": str(results)}
        return entry

    def run(self, pr, tmp_path, calls, dry_run=False):
        cache = str(tmp_path / "cache.json")
        got = pr_labels.label_pr("o/r", pr, cache, dry_run, calls["logs"].append)
        recorded = (
            pr_labels.labelled(json.load(open(cache))) if os.path.exists(cache) else {}
        )
        return got, recorded

    def test_decided_and_chosen_labels_are_added_and_the_pr_recorded(
        self, tmp_path, calls
    ):
        pr = self.pr(
            tmp_path, {"labels": ["pr/risk/net"], "reasons": {"pr/risk/net": "why"}}
        )
        got, recorded = self.run(pr, tmp_path, calls)
        assert got == ["deps", "pr/risk/net"]
        assert calls["add"] == [["deps", "pr/risk/net"]]
        assert recorded == {"9": ["deps", "pr/risk/net"]}
        assert any("pr/risk/net: why" in line for line in calls["logs"])

    def test_a_dry_run_adds_nothing_and_records_nothing(self, tmp_path, calls):
        pr = self.pr(tmp_path, {"labels": ["pr/risk/net"]})
        got, recorded = self.run(pr, tmp_path, calls, dry_run=True)
        assert got is None and calls["add"] == [] and recorded == {}
        assert any("would add deps, pr/risk/net" in line for line in calls["logs"])

    def test_a_subagent_with_no_usable_result_leaves_the_pr_for_the_next_review(
        self, tmp_path, calls
    ):
        pr = self.pr(tmp_path)
        pr["label_prompt"] = {"results_file": str(tmp_path / "never-written.json")}
        got, recorded = self.run(pr, tmp_path, calls)
        assert got is None and calls["add"] == [] and recorded == {}

    def test_a_failed_api_call_is_reported_and_the_pr_is_not_recorded(
        self, tmp_path, monkeypatch, calls
    ):
        def refuse(repo, n, labels):
            raise RuntimeError("HTTP 403")

        monkeypatch.setattr(pr_labels, "add_labels", refuse)
        got, recorded = self.run(self.pr(tmp_path, {"labels": []}), tmp_path, calls)
        assert got is None and recorded == {}
        assert any("HTTP 403" in line for line in calls["logs"])

    def test_nothing_to_add_still_records_the_pr(self, tmp_path, calls):
        pr = self.pr(tmp_path, {"labels": []}, decided=())
        got, recorded = self.run(pr, tmp_path, calls)
        assert got == [] and calls["add"] == [] and recorded == {"9": []}

    def test_an_entry_with_no_plan_is_left_alone(self, tmp_path, calls):
        got, recorded = self.run({"number": 9, "labels": None}, tmp_path, calls)
        assert got is None and calls["add"] == [] and recorded == {}

    def test_a_label_the_subagent_invents_is_never_added(self, tmp_path, calls):
        pr = self.pr(tmp_path, {"labels": ["pr/risk/net", "no-behavior-change"]})
        self.run(pr, tmp_path, calls)
        assert calls["add"] == [["deps", "pr/risk/net"]]


class TestAddLabels:
    def test_each_label_is_a_separate_field_of_one_issues_api_call(self, monkeypatch):
        seen = {}

        class Done:
            returncode = 0
            stderr = ""

        def run(argv, **kwargs):
            seen["argv"] = argv
            return Done()

        monkeypatch.setattr(pr_labels.subprocess, "run", run)
        pr_labels.add_labels("o/r", 5, ["pr/risk/net", "pr/tests-only"])
        assert seen["argv"] == [
            "gh",
            "api",
            "--method",
            "POST",
            "repos/o/r/issues/5/labels",
            "-f",
            "labels[]=pr/risk/net",
            "-f",
            "labels[]=pr/tests-only",
        ]

    def test_a_refusal_raises_with_the_reason(self, monkeypatch):
        class Refused:
            returncode = 1
            stderr = "HTTP 403: Resource not accessible"

        monkeypatch.setattr(pr_labels.subprocess, "run", lambda *a, **k: Refused())
        with pytest.raises(RuntimeError, match="HTTP 403"):
            pr_labels.add_labels("o/r", 5, ["x"])


class TestCollectApplies:
    def test_every_pr_in_the_manifest_with_a_plan_is_labelled_in_a_real_run(
        self, collect, monkeypatch
    ):
        seen = []
        monkeypatch.setattr(
            pr_labels,
            "label_pr",
            lambda repo, pr, cache, dry_run, log: seen.append(
                (repo, pr["number"], dry_run)
            ),
        )
        collect.apply_labels(
            {"prs": [{"number": 1}, {"number": 2}]}, "o/r", dry_run=False
        )
        assert seen == [("o/r", 1, False), ("o/r", 2, False)]

    def test_the_cache_it_records_into_is_the_review_cache(self, collect, monkeypatch):
        seen = []
        monkeypatch.setattr(
            pr_labels,
            "label_pr",
            lambda repo, pr, cache, dry_run, log: seen.append(cache),
        )
        collect.apply_labels({"prs": [{"number": 1}]}, "o/r", dry_run=True)
        assert seen[0].endswith(os.path.join(".ignore", "review-prs-cache.json"))
