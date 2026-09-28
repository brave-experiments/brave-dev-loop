"""Tests for the review-prs skill: incremental re-review, scoping, and the
detect -> select -> validate -> collect handoff."""

import json
import os
import shutil
import subprocess
import sys

import pytest
from conftest import ROOT_DIR, SKILLS_DIR, _load_module

SKILL_DIR = os.path.join(SKILLS_DIR, "review-prs")


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


def section(path, added, index="index 1111111..2222222 100644", hunk="@@ -1,2 +1,3 @@"):
    lines = [
        f"diff --git a/{path} b/{path}",
        index,
        f"--- a/{path}",
        f"+++ b/{path}",
        hunk,
        " context",
    ]
    lines += [f"+{line}" for line in added]
    return "\n".join(lines)


def diff_of(*sections):
    return "\n".join(sections) + "\n"


class TestDiffSections:
    def test_split_keeps_every_file_in_order(self, prep):
        a = section("browser/a.cc", ["int x;"])
        b = section("ui/b.ts", ["let y = 1"])
        sections = prep.split_diff(diff_of(a, b))
        assert list(sections) == ["browser/a.cc", "ui/b.ts"]
        assert sections["browser/a.cc"] == a
        assert prep.join_sections(sections.values()) == diff_of(a, b)

    def test_a_rebase_does_not_change_the_hash(self, prep):
        """New blob ids and moved hunks are what a rebase does to an unchanged change."""
        before = section("a.cc", ["int x;"])
        after = section(
            "a.cc",
            ["int x;"],
            index="index 3333333..4444444 100644",
            hunk="@@ -40,2 +41,3 @@ Foo",
        )
        assert prep.section_hash(before) == prep.section_hash(after)

    def test_a_different_change_does(self, prep):
        assert prep.section_hash(section("a.cc", ["int x;"])) != prep.section_hash(
            section("a.cc", ["int y;"])
        )


class TestOmission:
    @pytest.mark.parametrize(
        "path,body,reason",
        [
            ("ui/package-lock.json", "+{}", "lockfile"),
            ("Cargo.lock", "+x", "lockfile"),
            ("res/logo.png", "Binary files a/x and b/x differ", "binary"),
            ("app/res/icon.svg", "+<svg/>", "asset"),
            ("app/strings/fr.xtb", "+<x/>", "asset"),
            ("android/java/res/drawable-night/ic.xml", "+<vector/>", "drawable"),
            ("browser/a.cc", "+int x;", None),
            ("android/java/res/layout/main.xml", "+<x/>", None),
        ],
    )
    def test_reason(self, prep, path, body, reason):
        assert (
            prep.omitted_reason(path, f"diff --git a/{path} b/{path}\n{body}") == reason
        )

    def test_stub_says_what_changed(self, prep):
        s = "diff --git a/yarn.lock b/yarn.lock\nnew file mode 100644\n--- /dev/null\n+++ b/yarn.lock\n+a\n+b"
        assert prep.section_stub("yarn.lock", s, "lockfile") == (
            "yarn.lock (new file, +2/-0 lines, lockfile; content not shown)"
        )


class TestScope:
    FILES = [
        "browser/a.cc",
        "browser/BUILD.gn",
        "ui/b.ts",
        "android/java/C.java",
        "ios/D.swift",
        "app/generated_resources.grd",
    ]

    def test_filter_and_js_files_are_classified(self, prep):
        flags = prep.classify_files(["test/filters/browser_tests.filter", "ui/x.js"])
        assert flags["has_test_files"] and flags["has_frontend_files"]
        assert not flags["has_cpp_files"]

    def test_a_language_doc_skips_other_languages(self, prep):
        assert prep.files_in_scope("has_cpp_files", self.FILES) == [
            "browser/a.cc",
            "browser/BUILD.gn",
            "app/generated_resources.grd",
        ]
        assert prep.files_in_scope("has_frontend_files", self.FILES) == [
            "browser/BUILD.gn",
            "ui/b.ts",
            "app/generated_resources.grd",
        ]

    def test_other_docs_read_everything(self, prep):
        for condition in ("has_test_files", "has_build_files", "always", None):
            assert prep.files_in_scope(condition, self.FILES) == self.FILES

    def test_docs_for_flags_drops_unmet_conditions(self, prep):
        docs = [
            {"doc": "cpp.md", "condition": "has_cpp_files"},
            {"doc": "arch.md", "condition": "always"},
        ]
        flags = prep.classify_files(["scripts/x.py"])
        assert [d["doc"] for d in prep.docs_for_flags(docs, flags)] == ["arch.md"]


PR = {
    "number": 42,
    "title": "Add a thing",
    "headRefOid": "abc123",
    "author": "dev",
    "baseRefName": "main",
    "hasApproval": False,
    "isExternalContributor": False,
}


class TestProcessPr:
    CC = section("browser/a.cc", ["int x;"])
    TS = section("ui/b.ts", ["let y = 1"])
    LOCK = section("package-lock.json", ["{}"])

    @pytest.fixture
    def stubbed(self, prep, monkeypatch, tmp_dir):
        bp = os.path.join(tmp_dir, "cpp.md")
        with open(bp, "w") as f:
            f.write('# C++\n\n<a id="CS-001"></a>\n## One\nbody\n')
        diff = {"text": diff_of(self.CC, self.TS, self.LOCK)}
        monkeypatch.setattr(prep, "fetch_diff", lambda n: diff["text"])
        monkeypatch.setattr(prep, "fetch_prior_comments", lambda *a, **k: ("", False))
        monkeypatch.setattr(prep, "extract_images", lambda n: [])
        monkeypatch.setattr(
            prep,
            "resolve_bot_threads",
            lambda *a: {
                "resolved": 0,
                "unresolved_bot_threads": 0,
                "total_bot_threads": 0,
            },
        )
        monkeypatch.setattr(
            prep,
            "discover_best_practices",
            lambda flags: [
                {"doc": "cpp.md", "path": bp, "condition": "has_cpp_files"},
                {"doc": "frontend.md", "path": bp, "condition": "has_frontend_files"},
            ],
        )
        monkeypatch.setattr(
            prep, "fetch_and_create_worktree", lambda n, sha, path: path
        )
        return diff

    def _run(self, prep, tmp_dir, prior=None):
        work = os.path.join(tmp_dir, "work")
        os.makedirs(work, exist_ok=True)
        return prep.process_pr(PR, "bot", set(), work, True, prior)

    def _hashes(self, prep):
        return {
            p: prep.section_hash(s)
            for p, s in prep.split_diff(diff_of(self.CC, self.TS, self.LOCK)).items()
        }

    def test_first_review_reads_every_file(self, prep, stubbed, tmp_dir):
        result, error = self._run(prep, tmp_dir)
        assert error is None
        assert (result["files_reviewed"], result["files_total"]) == (3, 3)
        kinds = [(p["kind"], p["chunk_id"]) for p in result["subagent_prompts"]]
        assert kinds == [
            ("rules", "cpp.md_0"),
            ("rules", "frontend.md_0"),
            ("correctness", "correctness"),
        ]
        with open(result["subagent_prompts"][0]["prompt_file"]) as f:
            cpp_prompt = f.read()
        assert "+int x;" in cpp_prompt and "+let y = 1" not in cpp_prompt
        assert "package-lock.json (modified, +1/-0 lines, lockfile" in cpp_prompt
        assert "+{}" not in cpp_prompt
        with open(result["file_hashes_file"]) as f:
            assert json.load(f) == self._hashes(prep)

    def test_no_changed_file_stops_before_any_other_call(
        self, prep, stubbed, tmp_dir, monkeypatch
    ):
        def boom(*a, **k):
            raise AssertionError("an unchanged PR fetched more than its diff")

        for name in (
            "fetch_prior_comments",
            "extract_images",
            "fetch_and_create_worktree",
        ):
            monkeypatch.setattr(prep, name, boom)
        result, error = self._run(prep, tmp_dir, prior=self._hashes(prep))
        assert error is None
        assert result["unchanged"] is True
        assert result["headRefOid"] == "abc123"

    def test_a_re_review_reads_only_the_changed_files(self, prep, stubbed, tmp_dir):
        prior = self._hashes(prep)
        stubbed["text"] = diff_of(self.CC, section("ui/b.ts", ["let y = 2"]), self.LOCK)
        result, _ = self._run(prep, tmp_dir, prior=prior)
        assert (result["files_reviewed"], result["files_total"]) == (1, 3)
        assert result["rereview"] is True
        assert [p["chunk_id"] for p in result["subagent_prompts"]] == [
            "frontend.md_0",
            "correctness",
        ]
        with open(result["subagent_prompts"][0]["prompt_file"]) as f:
            prompt = f.read()
        assert "+let y = 2" in prompt and "+int x;" not in prompt
        assert "the other 2 were reviewed already" in prompt


class TestPrRemote:
    def test_ignores_a_remote_that_only_pushes_to_the_repo(self, prep, monkeypatch):
        repo = prep.PR_REPO
        out = (
            f"origin\tgit@github.com:netzenbot/fork.git (fetch)\n"
            f"origin\tssh://git@github.com/{repo}.git (push)\n"
            f"upstream\tgit@github.com:{repo}.git (fetch)\n"
            f"upstream\tssh://git@github.com/{repo}.git (push)\n"
        )
        monkeypatch.setattr(
            prep.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""),
        )
        prep.pr_remote.cache_clear()
        try:
            assert prep.pr_remote() == "upstream"
        finally:
            prep.pr_remote.cache_clear()


class TestPrioritize:
    def test_limit_widens_the_cap(self, post):
        vs = [{"file": f"f{i}", "line": 1, "severity": "medium"} for i in range(12)]
        kept, _ = post.prioritize_violations(list(vs), False)
        assert len(kept) == post.MAX_COMMENTS_PER_PR
        kept, _ = post.prioritize_violations(list(vs), False, limit=10)
        assert len(kept) == 10


BP_DOC = """# C++

<a id="CS-001"></a>
## One
Rule one text.

<a id="CS-002"></a>
## Two
Rule two text.
"""
LINK = "https://github.com/test-org/test-project/tree/main/docs/best-practices/cpp.md"


class TestSelectCandidates:
    @pytest.fixture
    def bp_dir(self, tmp_dir):
        d = os.path.join(tmp_dir, "bp")
        os.makedirs(d)
        with open(os.path.join(d, "cpp.md"), "w") as f:
            f.write(BP_DOC)
        return d

    def test_rule_text_finds_the_anchor(self, sel, bp_dir):
        assert "Rule two text." in sel.rule_text(f"{LINK}#CS-002", bp_dir)
        assert sel.rule_text(f"{LINK}#CS-999", bp_dir) is None

    def test_select_drops_what_would_not_post(self, sel, bp_dir):
        vs = [
            {
                "file": "a.cc",
                "line": 1,
                "severity": "medium",
                "rule_link": f"{LINK}#CS-001",
            },
            {
                "file": "a.cc",
                "line": 2,
                "severity": "medium",
                "rule_link": f"{LINK}#CS-999",
            },
            {"file": "a.cc", "line": 3, "severity": "low"},
            {"file": "a.cc", "line": 4, "severity": "high", "rule": "Use after move"},
            {
                "file": "a.cc",
                "line": 5,
                "severity": "medium",
                "rule_link": f"{LINK}#CS-001",
            },
            {
                "file": "b.cc",
                "line": 9,
                "severity": "medium",
                "rule_link": f"{LINK}#CS-002",
            },
        ]
        existing = [{"path": "b.cc", "line": 9, "body": "x", "user": "someone"}]
        kept = sel.select({"hasApproval": False}, vs, existing, bp_dir)
        assert [(v["file"], v["line"]) for v in kept] == [("a.cc", 4), ("a.cc", 1)]

    def test_select_caps_at_twice_the_posting_cap(self, sel, bp_dir):
        vs = [
            {
                "file": f"f{i}.cc",
                "line": 1,
                "severity": "medium",
                "rule_link": f"{LINK}#CS-001",
            }
            for i in range(30)
        ]
        kept = sel.select({"hasApproval": False}, vs, [], bp_dir)
        assert len(kept) == sel.CANDIDATE_LIMIT

    def test_main_writes_a_validator_per_pr_with_candidates(
        self, sel, bp_dir, tmp_dir, monkeypatch, capsys
    ):
        monkeypatch.setattr(sel._prep, "BP_DIR", bp_dir)
        monkeypatch.setattr(sel._post, "fetch_existing_comments", lambda r, n: [])
        work = os.path.join(tmp_dir, "work")
        prs = []
        for number, candidates in ((1, True), (2, False), (3, None)):
            pr_dir = os.path.join(work, f"pr_{number}")
            os.makedirs(pr_dir)
            hashes = os.path.join(pr_dir, "file_hashes.json")
            diff_file = os.path.join(pr_dir, "diff.patch")
            with open(diff_file, "w") as f:
                f.write(diff_of(section("a.cc", ["int x;"]), section("b.ts", ["y"])))
            results = os.path.join(pr_dir, "cpp.md_0_candidates.json")
            if candidates is not None:
                vs = (
                    [
                        {
                            "file": "a.cc",
                            "line": 2,
                            "severity": "medium",
                            "rule_link": f"{LINK}#CS-001",
                        }
                    ]
                    if candidates
                    else []
                )
                with open(results, "w") as f:
                    json.dump({"violations": vs}, f)
            prs.append(
                {
                    "number": number,
                    "title": f"PR {number}",
                    "file_hashes_file": hashes,
                    "diff_file": diff_file,
                    "source_path": "/src",
                    "subagent_prompts": [
                        {"chunk_id": "cpp.md_0", "results_file": results}
                    ],
                }
            )
        with open(os.path.join(work, "manifest.json"), "w") as f:
            json.dump({"pr_repo": "o/r", "bot_username": "bot", "prs": prs}, f)

        monkeypatch.setattr(sys, "argv", ["select-candidates.py", "--work-dir", work])
        sel.main()
        out = json.loads(capsys.readouterr().out)
        assert [v["pr"] for v in out["validators"]] == [1]
        assert out["incomplete"] == [3]

        with open(os.path.join(work, "manifest.json")) as f:
            by_number = {p["number"]: p for p in json.load(f)["prs"]}
        assert by_number[1]["validation"]["candidates"] == 1
        assert by_number[2]["validation"] is None
        assert by_number[3]["review_incomplete"] is True
        with open(by_number[1]["validation"]["prompt_file"]) as f:
            prompt = f.read()
        assert "Rule one text." in prompt
        assert "+int x;" in prompt and "+y" not in prompt
        assert "The source tree at the PR head is at: /src" in prompt


class TestCollect:
    def _pr(self, tmp_dir, **extra):
        pr = {
            "number": 5,
            "title": "t",
            "headRefOid": "sha",
            "file_hashes_file": "/h.json",
        }
        pr.update(extra)
        return pr

    def test_skip_rules(self, collect, tmp_dir):
        validated = os.path.join(tmp_dir, "validated.json")
        with open(validated, "w") as f:
            json.dump({"violations": [{"file": "a.cc"}], "validation_log": ["x"]}, f)
        cases = [
            (self._pr(tmp_dir, review_incomplete=True, validation=None), True),
            (self._pr(tmp_dir), True),
            (self._pr(tmp_dir, validation={"results_file": "/nope.json"}), True),
            (self._pr(tmp_dir, validation=None), False),
            (self._pr(tmp_dir, validation={"results_file": validated}), False),
        ]
        for pr, skipped in cases:
            _, _, reason = collect.collect_violations(pr)
            assert bool(reason) is skipped, pr

    def test_skipped_prs_are_not_posted_or_cached(self, collect, tmp_dir):
        manifest = {
            "prs": [
                self._pr(tmp_dir, number=1, validation=None),
                self._pr(tmp_dir, number=2, validation={"results_file": "/nope.json"}),
            ]
        }
        results = collect.build_post_review_input(manifest)["pr_results"]
        assert [r["number"] for r in results] == [1]
        assert results[0]["fileHashesFile"] == "/h.json"
        assert results[0]["violations"] == []


class TestUpdateCache:
    """update-cache.py writes .ignore/ under its own bot dir, so run a copy."""

    @pytest.fixture
    def bot(self, tmp_dir):
        skill = os.path.join(tmp_dir, ".claude", "skills", "review-prs")
        os.makedirs(skill)
        shutil.copy(os.path.join(SKILL_DIR, "update-cache.py"), skill)
        os.symlink(
            os.path.abspath(os.path.join(ROOT_DIR, "scripts")),
            os.path.join(tmp_dir, "scripts"),
        )
        os.makedirs(os.path.join(tmp_dir, ".ignore"))
        return tmp_dir

    def _run(self, bot, *args):
        r = subprocess.run(
            [
                sys.executable,
                os.path.join(bot, ".claude", "skills", "review-prs", "update-cache.py"),
                *args,
            ],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr
        with open(os.path.join(bot, ".ignore", "review-prs-cache.json")) as f:
            return json.load(f)

    def test_records_file_hashes(self, bot, tmp_dir):
        hashes = os.path.join(tmp_dir, "h.json")
        with open(hashes, "w") as f:
            json.dump({"a.cc": "1234"}, f)
        cache = self._run(bot, "7", "sha7", f"--file-hashes={hashes}")
        assert cache["7"] == "sha7"
        assert cache["_files"] == {"7": {"a.cc": "1234"}}
        cache = self._run(bot, "7", "sha8")
        assert cache["_files"] == {"7": {"a.cc": "1234"}}

    def test_trims_the_oldest_entries(self, bot, tmp_dir):
        with open(os.path.join(bot, ".ignore", "review-prs-cache.json"), "w") as f:
            json.dump({"_files": {str(i): {} for i in range(500)}}, f)
        hashes = os.path.join(tmp_dir, "h.json")
        with open(hashes, "w") as f:
            json.dump({}, f)
        files = self._run(bot, "3", "sha", f"--file-hashes={hashes}")["_files"]
        assert len(files) == 500 and list(files)[-1] == "3"
        files = self._run(bot, "900", "sha", f"--file-hashes={hashes}")["_files"]
        assert len(files) == 500
        assert "0" not in files and list(files)[-1] == "900"
