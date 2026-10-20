"""review-prs: linking a finding to the rule it breaks, and proposing a new rule.

The bug pass and the project-guidance pass write findings with no rule link, so
the validator names the rule (`cited_rule_link`) when a profile asks for it, and
may describe one rule the project lacks, which collect-results.py files as an
issue.
"""

import json
import os

import pytest
from conftest import ROOT_DIR, SKILLS_DIR, _load_module

SKILL_DIR = os.path.join(SKILLS_DIR, "review-prs")
BP_LINK = "https://github.com/test-org/test-project/tree/main/docs/best-practices"

TESTS_DOC = """# Tests

<!-- applicability: always -->

Intro text that is not a rule.

---

<a id="TS-001"></a>

## Tests are behavioural

**A test name says the property, not the function under test.** The doc comment
says why the property matters, `not` what the test does.

---

<a id="TS-002"></a>

## Test refusals

A change that adds a gate is covered when the denied path is asserted.

## Not anchored, so not citable

Nothing can link to this.
"""

SPECS_DOC = (
    """# Specs

<a id="SP-001"></a>
## A change agrees with its spec

A word. """
    + ("long " * 80)
    + """end.
"""
)


@pytest.fixture(scope="module")
def prep():
    return _load_module("prepare_review", os.path.join(SKILL_DIR, "prepare-review.py"))


@pytest.fixture(scope="module")
def post():
    return _load_module("post_review", os.path.join(SKILL_DIR, "post-review.py"))


@pytest.fixture(scope="module")
def sel():
    return _load_module(
        "select_candidates", os.path.join(SKILL_DIR, "select-candidates.py")
    )


@pytest.fixture(scope="module")
def collect():
    return _load_module(
        "collect_results", os.path.join(SKILL_DIR, "collect-results.py")
    )


@pytest.fixture(scope="module")
def fp():
    return _load_module("file_proposals", os.path.join(SKILL_DIR, "file-proposals.py"))


@pytest.fixture(scope="module")
def ri(prep):
    """scripts/lib/rule_index.py, importable once prepare-review put scripts/ on the path."""
    import lib.rule_index

    return lib.rule_index


@pytest.fixture
def bp_dir(tmp_dir):
    d = os.path.join(tmp_dir, "best-practices")
    os.makedirs(d)
    for name, text in (("tests.md", TESTS_DOC), ("specs.md", SPECS_DOC)):
        with open(os.path.join(d, name), "w") as f:
            f.write(text)
    return d


class TestRuleIndex:
    def test_only_anchored_rules_are_listed(self, ri, bp_dir):
        rules = ri.rules_in(os.path.join(bp_dir, "tests.md"))
        assert [(r["id"], r["heading"]) for r in rules] == [
            ("TS-001", "Tests are behavioural"),
            ("TS-002", "Test refusals"),
        ]

    def test_the_summary_is_the_first_paragraph_flattened(self, ri, bp_dir):
        first = ri.rules_in(os.path.join(bp_dir, "tests.md"))[0]
        assert first["summary"] == (
            "A test name says the property, not the function under test. The doc "
            "comment says why the property matters, not what the test does."
        )

    def test_a_long_summary_is_cut_at_a_word(self, ri, bp_dir):
        [rule] = ri.rules_in(os.path.join(bp_dir, "specs.md"))
        assert len(rule["summary"]) <= ri.SUMMARY_CHARS + 3
        assert rule["summary"].endswith("...") and " ..." not in rule["summary"]

    def test_it_lists_what_the_chunker_hands_the_detect_passes(
        self, ri, bp_dir, chunk_best_practices
    ):
        """An id the index offers must be one a detect pass could have been given."""
        for doc in ("tests.md", "specs.md"):
            path = os.path.join(bp_dir, doc)
            with open(path) as f:
                _, rules = chunk_best_practices.split_into_rules(f.read())
            from_chunker = {
                part.split('"')[1]
                for r in rules
                for part in r["text"].split("<a id=")[1:]
            }
            assert {r["id"] for r in ri.rules_in(path)} == from_chunker

    def test_urls_are_the_ones_the_detect_prompt_asks_for(self, ri, bp_dir):
        index = ri.index_for(bp_dir, ["tests.md", "tests.md", "nope.md"], BP_LINK)
        assert [r["url"] for r in index] == [
            f"{BP_LINK}/tests.md#TS-001",
            f"{BP_LINK}/tests.md#TS-002",
        ]

    def test_a_link_to_a_missing_anchor_names_no_rule(self, ri, bp_dir):
        assert ri.heading_of(f"{BP_LINK}/tests.md#TS-002", bp_dir) == "Test refusals"
        assert ri.heading_of(f"{BP_LINK}/tests.md#TS-999", bp_dir) is None
        assert ri.heading_of(f"{BP_LINK}/nope.md#TS-001", bp_dir) is None
        assert ri.heading_of("https://example.com/", bp_dir) is None
        assert ri.names_no_rule(f"{BP_LINK}/tests.md#TS-999", bp_dir) is True
        # Not judged: no such doc to compare with, or not a rule link at all.
        assert ri.names_no_rule(f"{BP_LINK}/nope.md#TS-001", bp_dir) is False
        assert ri.names_no_rule("https://example.com/", bp_dir) is False

    def test_a_link_cannot_reach_a_doc_outside_the_directory(self, ri, bp_dir):
        """Only the file name in the URL is looked up, and only inside `bp_dir`."""
        with open(os.path.join(os.path.dirname(bp_dir), "outside.md"), "w") as f:
            f.write('<a id="OUT-1"></a>\n## Outside\n\nNot a rule of this project.\n')
        for link in (
            f"{BP_LINK}/../outside.md#OUT-1",
            f"{BP_LINK}/%2e%2e/outside.md#OUT-1",
            "https://example.com//../../outside.md#OUT-1",
        ):
            assert ri.heading_of(link, bp_dir) is None, link


class TestProfileFlags:
    def test_both_need_a_json_true(self, prep):
        for value in ("true", 1, None, "yes"):
            profile = {"review": {"linkRules": value, "proposeBestPractices": value}}
            assert prep.review_link_rules(profile) is False
            assert prep.review_propose_practices(profile) is False
        on = {"review": {"linkRules": True, "proposeBestPractices": True}}
        assert prep.review_link_rules(on) is True
        assert prep.review_propose_practices(on) is True
        assert prep.review_link_rules({}) is False
        assert prep.review_propose_practices({"review": "text"}) is False

    def test_bravebot_asks_for_both_and_brave_core_for_neither(self, prep):
        for name in ("bravebot", "brave-core"):
            profile = prep.load_profile({"project": {"profile": name}}, ROOT_DIR)
            asked = name == "bravebot"
            assert prep.review_link_rules(profile) is asked, name
            assert prep.review_propose_practices(profile) is asked, name


INDEX = [
    {
        "url": f"{BP_LINK}/tests.md#TS-001",
        "heading": "Tests are behavioural",
        "summary": "A test name says the property.",
    }
]


class TestValidatorPrompt:
    CTX = {"number": 1, "title": "t", "bot_username": "bot"}
    ARGS = ([{"id": "c1"}], [], "", {}, [], "/src", "/out.json")

    def build(self, prep, **ctx):
        return prep.build_validate_prompt(dict(self.CTX, **ctx), *self.ARGS)

    def test_without_the_flags_the_prompt_is_unchanged(self, prep):
        prompt = self.build(prep)
        for text in ("Rule index", "cited_rule_link", "proposals"):
            assert text not in prompt

    def test_the_index_and_the_field_come_with_link_rules(self, prep):
        prompt = self.build(
            prep, link_rules=True, rule_index=INDEX, docs_dir="docs/best-practices"
        )
        assert (
            f"- {BP_LINK}/tests.md#TS-001 — Tests are behavioural: "
            "A test name says the property."
        ) in prompt
        assert "`cited_rule_link`" in prompt
        assert "`rule_link` is not the field for it" in prompt
        assert "`/src/docs/best-practices/<doc>`" in prompt
        assert "proposals" not in prompt

    def test_no_read_hint_when_the_docs_sit_outside_the_repo(self, prep):
        prompt = self.build(prep, link_rules=True, rule_index=INDEX, docs_dir="")
        assert "`cited_rule_link`" in prompt and "Read the rule in full" not in prompt

    def test_an_empty_index_asks_for_no_link(self, prep):
        assert "cited_rule_link" not in self.build(prep, link_rules=True, rule_index=[])

    def test_proposals_are_asked_for_and_shown_in_the_output_shape(self, prep):
        prompt = self.build(prep, propose=True, rule_index=INDEX)
        assert "Best practice proposals:" in prompt
        assert '"proposals": [{"title"' in prompt
        assert "Rule index" in prompt  # to avoid proposing a rule it already has
        assert "cited_rule_link" not in prompt


def pr_with_docs(tmp_dir, docs=("tests.md", "specs.md"), **extra):
    os.makedirs(os.path.join(tmp_dir, "pr"), exist_ok=True)
    hashes = os.path.join(tmp_dir, "pr", "file_hashes.json")
    with open(hashes, "w") as f:
        f.write("{}")
    pr = {
        "number": 7,
        "title": "t",
        "file_hashes_file": hashes,
        "subagent_prompts": [{"kind": "rules", "doc": d} for d in docs]
        + [{"kind": "correctness"}, {"kind": "project"}],
    }
    pr.update(extra)
    return pr


CANDIDATE = {
    "file": "a.rs",
    "line": 3,
    "severity": "high",
    "rule": "Untested wiring",
    "issue": "x",
    "draft_comment": "This wiring is untested.",
}


class TestSelectCandidates:
    def write(self, sel, monkeypatch, tmp_dir, bp_dir, link, propose, **pr_extra):
        monkeypatch.setattr(sel._prep, "BP_DIR", bp_dir)
        monkeypatch.setattr(sel._prep, "BP_LINK_BASE", BP_LINK)
        monkeypatch.setattr(sel._prep, "REVIEW_LINK_RULES", link)
        monkeypatch.setattr(sel._prep, "REVIEW_PROPOSE_PRACTICES", propose)
        pr = pr_with_docs(tmp_dir, **pr_extra)
        info = sel.write_validator(pr, [CANDIDATE], "bot", bp_dir)
        with open(info["prompt_file"]) as f:
            return f.read()

    def test_the_index_lists_the_rules_the_pr_was_checked_against(
        self, sel, monkeypatch, tmp_dir, bp_dir
    ):
        prompt = self.write(
            sel, monkeypatch, tmp_dir, bp_dir, True, False, docs=("tests.md",)
        )
        assert f"{BP_LINK}/tests.md#TS-001" in prompt
        assert f"{BP_LINK}/tests.md#TS-002" in prompt
        assert "specs.md#SP-001" not in prompt  # a doc that did not apply to the PR
        assert "TS-003" not in prompt

    def test_neither_flag_means_no_index(self, sel, monkeypatch, tmp_dir, bp_dir):
        prompt = self.write(sel, monkeypatch, tmp_dir, bp_dir, False, False)
        assert "Rule index" not in prompt and "cited_rule_link" not in prompt
        assert "proposals" not in prompt

    def test_proposals_alone_still_get_the_index_but_no_link_field(
        self, sel, monkeypatch, tmp_dir, bp_dir
    ):
        prompt = self.write(sel, monkeypatch, tmp_dir, bp_dir, False, True)
        assert "Rule index" in prompt and "Best practice proposals:" in prompt
        assert "cited_rule_link" not in prompt


def validated(violations, proposals=None):
    data = {"violations": violations, "validation_log": []}
    if proposals is not None:
        data["proposals"] = proposals
    return data


class TestCollect:
    def violation(self, **kw):
        return dict(CANDIDATE, **kw)

    def test_a_cited_link_that_names_a_rule_is_kept_with_the_docs_heading(
        self, collect, bp_dir
    ):
        [v] = collect.settle_rule_links(
            [
                self.violation(
                    cited_rule_link=f"{BP_LINK}/tests.md#TS-002",
                    cited_rule="whatever the model called it",
                )
            ],
            bp_dir,
        )
        assert v["cited_rule_link"] == f"{BP_LINK}/tests.md#TS-002"
        assert v["cited_rule"] == "Test refusals"
        assert "rule_link" not in v  # dedup keeps treating it as an unlinked finding

    @pytest.mark.parametrize(
        "link",
        [
            f"{BP_LINK}/tests.md#TS-999",
            f"{BP_LINK}/nope.md#TS-001",
            "https://example.com/phish",
            "",
        ],
    )
    def test_a_cited_link_that_names_no_rule_is_dropped_and_the_finding_stays(
        self, collect, bp_dir, link
    ):
        [v] = collect.settle_rule_links(
            [self.violation(cited_rule_link=link, cited_rule="x")], bp_dir
        )
        assert "cited_rule_link" not in v and "cited_rule" not in v
        assert v["draft_comment"] == "This wiring is untested."

    def test_nothing_is_cited_when_the_docs_are_unknown(self, collect):
        [v] = collect.settle_rule_links(
            [self.violation(cited_rule_link=f"{BP_LINK}/tests.md#TS-001")], None
        )
        assert "cited_rule_link" not in v

    def test_a_finding_already_linked_ignores_a_cited_link(self, collect, bp_dir):
        [v] = collect.settle_rule_links(
            [
                self.violation(
                    rule_link=f"{BP_LINK}/tests.md#TS-001",
                    cited_rule_link=f"{BP_LINK}/tests.md#TS-002",
                )
            ],
            bp_dir,
        )
        assert v["rule_link"].endswith("#TS-001") and "cited_rule_link" not in v

    def test_a_rule_link_the_validator_changed_to_nothing_real_is_dropped(
        self, collect, bp_dir
    ):
        bad = f"{BP_LINK}/tests.md#TS-999"
        [v] = collect.settle_rule_links(
            [
                self.violation(
                    severity="medium",
                    rule_link=bad,
                    draft_comment=f"Wrong. ([Rule]({bad}))",
                )
            ],
            bp_dir,
        )
        assert "rule_link" not in v and v["draft_comment"] == "Wrong."

    def test_collect_violations_settles_links_from_the_results_file(
        self, collect, bp_dir, tmp_dir
    ):
        path = os.path.join(tmp_dir, "validated.json")
        with open(path, "w") as f:
            json.dump(
                validated(
                    [
                        self.violation(cited_rule_link=f"{BP_LINK}/tests.md#TS-001"),
                        self.violation(
                            line=9, cited_rule_link=f"{BP_LINK}/tests.md#NO"
                        ),
                    ]
                ),
                f,
            )
        pr = {"number": 1, "validation": {"results_file": path}}
        violations, _, reason = collect.collect_violations(pr, bp_dir)
        assert reason is None
        assert ["cited_rule_link" in v for v in violations] == [True, False]

    def test_proposals_reach_the_post_review_input(self, collect, tmp_dir):
        path = os.path.join(tmp_dir, "validated.json")
        proposal = {"title": "A rule", "rule": "r", "why": "w"}
        with open(path, "w") as f:
            json.dump(validated([], [proposal, "not a dict"]), f)
        pr = {
            "number": 1,
            "title": "t",
            "headRefOid": "sha",
            "file_hashes_file": "/h.json",
            "validation": {"results_file": path},
        }
        [result] = collect.build_post_review_input({"prs": [pr]})["pr_results"]
        assert result["proposals"] == [proposal]

    def test_a_results_file_without_proposals_has_none(self, collect, tmp_dir):
        path = os.path.join(tmp_dir, "validated.json")
        with open(path, "w") as f:
            json.dump(validated([]), f)
        assert collect.collect_proposals({"validation": {"results_file": path}}) == []
        assert collect.collect_proposals({"validation": None}) == []

    def test_a_failure_filing_proposals_never_fails_the_run(
        self, collect, monkeypatch, capsys
    ):
        monkeypatch.setattr(collect, "SCRIPT_DIR", "/nowhere")
        collect.file_best_practice_proposals(
            [{"proposals": [{}]}], "o/r", "o/r", "/bp", True
        )
        assert "proposals not filed" in capsys.readouterr().err


class TestEmbedCitedLink:
    def test_the_link_is_appended_with_the_docs_heading(self, post):
        v = dict(
            CANDIDATE,
            cited_rule_link=f"{BP_LINK}/tests.md#TS-002",
            cited_rule="Test refusals",
        )
        post.embed_rule_link_in_comment(v)
        assert v["draft_comment"] == (
            f"This wiring is untested. ([Test refusals]({BP_LINK}/tests.md#TS-002))"
        )

    def test_a_rule_link_still_wins(self, post):
        v = dict(
            CANDIDATE,
            rule="Tests are behavioural",
            rule_link=f"{BP_LINK}/tests.md#TS-001",
            cited_rule_link=f"{BP_LINK}/tests.md#TS-002",
        )
        post.embed_rule_link_in_comment(v)
        assert v["draft_comment"].endswith(
            f"([Tests are behavioural]({BP_LINK}/tests.md#TS-001))"
        )

    def test_a_link_already_in_the_comment_is_not_repeated(self, post):
        link = f"{BP_LINK}/tests.md#TS-002"
        v = dict(CANDIDATE, draft_comment=f"See [it]({link}).", cited_rule_link=link)
        post.embed_rule_link_in_comment(v)
        assert v["draft_comment"] == f"See [it]({link})."

    def test_a_cited_link_does_not_make_two_findings_in_a_file_one(self, post):
        """`rule_link` is a dedup key: two findings with the same one are the same
        rule broken twice. A link the validator merely cited says no such thing."""
        link = f"{BP_LINK}/tests.md#TS-001"
        a = dict(CANDIDATE, line=3, issue="first wiring", cited_rule_link=link)
        b = dict(CANDIDATE, line=40, issue="a different test", cited_rule_link=link)
        assert len(post.deduplicate_batch_violations([a, b])) == 2
        linked = [dict(a, rule_link=link), dict(b, rule_link=link)]
        assert len(post.deduplicate_batch_violations(linked)) == 1


PROPOSAL = {
    "title": "Assert the fault a test claims to reject",
    "doc": "tests.md",
    "rule": "A test that names a fault asserts that the fault fails it.",
    "why": "A reviewer has to read the diff to see whether the assertion would fail.",
    "evidence": [{"file": "crates/a/src/b.rs", "line": 12, "note": "never fails"}],
}
REPO = "test-org/test-project"


def pr_of(number=7, **proposal):
    return {
        "number": number,
        "title": "t",
        "headRefOid": "abc123",
        "proposals": [dict(PROPOSAL, **proposal)],
    }


class FakeGh:
    """Stands in for `gh`: records the calls, answers `issue list` and `issue create`."""

    def __init__(self, issues=(), list_rc=0, create_rc=0):
        self.issues, self.list_rc, self.create_rc = list(issues), list_rc, create_rc
        self.created = []
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[:3] == ["gh", "issue", "list"]:
            return self.list_rc, json.dumps(self.issues), "boom" * (self.list_rc != 0)
        title = argv[argv.index("--title") + 1]
        body = argv[argv.index("--body") + 1]
        self.created.append((title, body))
        n = 100 + len(self.created)
        return self.create_rc, f"https://github.com/{REPO}/issues/{n}\n", "denied"


class TestCleaning:
    def test_mentions_are_defused_and_foreign_links_removed(self, fp):
        text = fp.clean(
            "ask @someone and @org/team, see https://evil.example/x and "
            f"https://github.com/{REPO}/pull/3, mail a@b.c",
            400,
            REPO,
        )
        assert "`@someone`" in text and "`@org/team`" in text
        assert "evil.example" not in text and "[link removed]" in text
        assert f"https://github.com/{REPO}/pull/3" in text
        assert "a@b.c" in text  # not a mention

    def test_control_characters_go_and_length_is_bounded(self, fp):
        assert fp.clean("a\x1b[31mb\x00c", 50, REPO, one_line=True) == "a[31mbc"
        assert len(fp.clean("x" * 500, 20, REPO)) == 20
        assert fp.clean(None, 20, REPO) == ""

    def test_a_thin_or_malformed_proposal_is_not_a_rule(self, fp):
        assert fp.normalize("text", REPO) is None
        assert fp.normalize(dict(PROPOSAL, title="short"), REPO) is None
        assert fp.normalize(dict(PROPOSAL, rule=""), REPO) is None
        assert fp.normalize(dict(PROPOSAL, why="too short"), REPO) is None
        assert fp.normalize(PROPOSAL, REPO)["title"] == PROPOSAL["title"]

    def test_evidence_must_be_a_plain_relative_path_and_a_line(self, fp):
        bad = [
            {"file": "/etc/passwd", "line": 1},
            {"file": "../x.rs", "line": 1},
            {"file": ".hidden", "line": 1},
            {"file": "a b.rs", "line": 1},
            {"file": "a.rs", "line": 0},
            {"file": "a.rs", "line": "3"},
            "a.rs:3",
        ]
        good = [{"file": f"f{i}.rs", "line": i + 1, "note": "n"} for i in range(5)]
        p = fp.normalize(dict(PROPOSAL, evidence=bad + good), REPO)
        assert [e["file"] for e in p["evidence"]] == ["f0.rs", "f1.rs", "f2.rs"]


class TestIssueText:
    def test_the_issue_names_the_pr_and_links_the_evidence_at_its_commit(self, fp):
        title, body = fp.build_issue(fp.normalize(PROPOSAL, REPO), pr_of(), REPO)
        assert title == "Best practice: Assert the fault a test claims to reject"
        assert body.startswith(fp.MARKER)
        assert f"[PR #7](https://github.com/{REPO}/pull/7)" in body
        assert "nothing has been added to the docs" in body
        assert (
            "## Proposed rule" in body and "## Why a reviewer has to decide it" in body
        )
        assert f"https://github.com/{REPO}/blob/abc123/crates/a/src/b.rs#L12" in body
        assert "Suggested home: `tests.md`." in body

    def test_an_issue_without_evidence_or_commit_still_reads(self, fp):
        p = fp.normalize(dict(PROPOSAL, evidence=[], doc=""), REPO)
        _, body = fp.build_issue(p, {"number": 7}, REPO)
        assert "Where it showed up" not in body and "Suggested home" not in body


class TestFiling:
    def file(self, fp, prs, gh, bp_dir, **kw):
        lines = []
        out = fp.file_proposals(prs, REPO, REPO, bp_dir, run=gh, say=lines.append, **kw)
        return out, lines

    def test_a_proposal_becomes_one_issue(self, fp, bp_dir):
        gh = FakeGh()
        out, _ = self.file(fp, [pr_of()], gh, bp_dir)
        assert [o["status"] for o in out] == ["filed"]
        assert out[0]["url"] == f"https://github.com/{REPO}/issues/101"
        [(title, body)] = gh.created
        assert title.startswith("Best practice: ") and "Proposed rule" in body
        create = gh.calls[-1]
        assert create[:5] == ["gh", "issue", "create", "--repo", REPO]
        assert "--label" not in create and "--assignee" not in create

    def test_nothing_is_listed_or_filed_when_there_is_nothing_to_file(self, fp, bp_dir):
        gh = FakeGh()
        assert self.file(fp, [], gh, bp_dir)[0] == []
        assert (
            self.file(fp, [{"number": 1, "proposals": [{"title": "x"}]}], gh, bp_dir)[0]
            == []
        )
        assert gh.calls == []

    def test_a_dry_run_files_nothing(self, fp, bp_dir):
        gh = FakeGh()
        out, lines = self.file(fp, [pr_of()], gh, bp_dir, dry_run=True)
        assert [o["status"] for o in out] == ["dry-run"] and gh.created == []
        assert any("not an auto run" in line for line in lines)

    def test_a_rule_the_docs_already_have_is_not_proposed(self, fp, bp_dir):
        gh = FakeGh()
        out, lines = self.file(fp, [pr_of(title="Tests are behavioural")], gh, bp_dir)
        assert [o["status"] for o in out] == ["duplicate"] and gh.created == []
        assert any("already exists" in line for line in lines)

    def test_an_issue_that_already_proposes_it_is_not_filed_again(self, fp, bp_dir):
        old = {
            "title": "Best practice: Assert the fault that a test claims to reject",
            "url": f"https://github.com/{REPO}/issues/9",
            "body": "",
        }
        out, lines = self.file(fp, [pr_of()], FakeGh([old]), bp_dir)
        assert [o["status"] for o in out] == ["duplicate"]
        assert any("/issues/9 already proposes it" in line for line in lines)

    def test_a_pr_that_already_drew_a_proposal_draws_no_second(self, fp, bp_dir):
        old = {
            "title": "Best practice: Something else entirely",
            "url": f"https://github.com/{REPO}/issues/9",
            "body": f"Proposed while reviewing [PR #7](https://github.com/{REPO}/pull/7).",
        }
        out, _ = self.file(fp, [pr_of(7)], FakeGh([old]), bp_dir)
        assert [o["status"] for o in out] == ["duplicate"]
        # PR 70 is a different pull request, not a prefix match for PR 7.
        out, _ = self.file(fp, [pr_of(70)], FakeGh([old]), bp_dir)
        assert [o["status"] for o in out] == ["filed"]

    def test_an_issue_that_is_not_a_proposal_is_not_a_reason_to_skip(self, fp, bp_dir):
        other = {
            "title": "Assert the fault a test claims to reject",
            "url": f"https://github.com/{REPO}/issues/9",
            "body": f"https://github.com/{REPO}/pull/7",
        }
        out, _ = self.file(fp, [pr_of()], FakeGh([other]), bp_dir)
        assert [o["status"] for o in out] == ["filed"]

    def test_two_prs_proposing_the_same_rule_file_it_once(self, fp, bp_dir):
        gh = FakeGh()
        out, _ = self.file(fp, [pr_of(1), pr_of(2)], gh, bp_dir)
        assert [o["status"] for o in out] == ["filed", "duplicate"]
        assert len(gh.created) == 1

    def test_at_most_one_per_pr_and_a_few_per_run(self, fp, bp_dir):
        titles = [
            "Pin every spawned program to an absolute path",
            "Bound each retry loop with a deadline",
            "Name the owner of every temporary directory",
            "Record why a fallback exists beside the fallback",
        ]
        prs = [pr_of(n, title=t) for n, t in enumerate(titles, 1)]
        prs[0]["proposals"].append(
            dict(PROPOSAL, title="A second one for the first PR")
        )
        gh = FakeGh()
        out, _ = self.file(fp, prs, gh, bp_dir)
        assert [o["status"] for o in out] == ["filed"] * 3 + ["capped"]
        assert len(gh.created) == fp.MAX_PER_RUN
        assert "A second one" not in "".join(t for t, _ in gh.created)

    def test_unlisted_issues_mean_nothing_is_filed(self, fp, bp_dir):
        gh = FakeGh(list_rc=1)
        out, lines = self.file(fp, [pr_of()], gh, bp_dir)
        assert out == [] and gh.created == []
        assert any("could not be checked" in line for line in lines)

    def test_a_refused_create_is_reported_not_raised(self, fp, bp_dir):
        out, lines = self.file(fp, [pr_of()], FakeGh(create_rc=1), bp_dir)
        assert [o["status"] for o in out] == ["failed"]
        assert any("issue not created: denied" in line for line in lines)

    def test_model_text_is_cleaned_before_it_is_posted(self, fp, bp_dir):
        gh = FakeGh()
        hostile = dict(
            PROPOSAL,
            rule="Ping @victim and read https://evil.example/payload for the rule.",
        )
        self.file(fp, [dict(pr_of(), proposals=[hostile])], gh, bp_dir)
        [(_, body)] = gh.created
        assert "`@victim`" in body and "evil.example" not in body
