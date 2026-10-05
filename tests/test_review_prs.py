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
def pr_diff(prep):
    """scripts/lib/pr_diff.py, importable once prepare-review put scripts/ on the path."""
    import lib.pr_diff

    return lib.pr_diff


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


class TestDiffPastTheFileLimit:
    """GitHub refuses the whole-PR diff past 300 files, which a Chromium
    upgrade always is. The files API pages through up to 3000, each with the
    same hunk text, and the diff is rebuilt from it."""

    @staticmethod
    def _gh(
        monkeypatch,
        prep,
        files,
        diff_stderr="HTTP 406: Sorry, the diff exceeded the maximum number of files (300). (too_large)",
    ):
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            if args[:3] == ["gh", "pr", "diff"]:
                return subprocess.CompletedProcess(args, 1, "", diff_stderr)
            out = "".join(json.dumps(f) + "\n" for f in files)
            return subprocess.CompletedProcess(args, 0, out, "")

        monkeypatch.setattr(prep.subprocess, "run", run)
        return calls

    def test_a_diff_too_large_is_rebuilt_file_by_file(self, prep, monkeypatch):
        a = section("browser/a.cc", ["int x;"])
        patch = a.split("\n", 4)[4]
        calls = self._gh(
            monkeypatch,
            prep,
            [
                {
                    "filename": "browser/a.cc",
                    "status": "modified",
                    "changes": 1,
                    "patch": patch,
                }
            ],
        )
        text = prep.fetch_diff(1)
        assert "--paginate" in calls[1]
        sections = prep.split_diff(text)
        assert prep.section_hash(sections["browser/a.cc"]) == prep.section_hash(a)
        assert prep.parse_diff_line_ranges(text) == prep.parse_diff_line_ranges(
            diff_of(a)
        )

    def test_any_other_failure_is_still_an_error(self, prep, monkeypatch):
        calls = self._gh(monkeypatch, prep, [], diff_stderr="HTTP 404: Not Found")
        with pytest.raises(RuntimeError, match="Not Found"):
            prep.fetch_diff(1)
        assert len(calls) == 1

    def test_headers_follow_the_status(self, prep, pr_diff):
        added = pr_diff.file_section(
            {"filename": "n.cc", "status": "added", "patch": "@@ -0,0 +1 @@\n+x"}
        )
        removed = pr_diff.file_section(
            {"filename": "o.cc", "status": "removed", "patch": "@@ -1 +0,0 @@\n-x"}
        )
        moved = pr_diff.file_section(
            {
                "filename": "b.cc",
                "previous_filename": "a.cc",
                "status": "renamed",
                "changes": 0,
            }
        )
        assert (
            "new file mode" in added
            and "--- /dev/null" in added
            and "+++ b/n.cc" in added
        )
        assert "deleted file mode" in removed and "+++ /dev/null" in removed
        assert prep.split_diff(moved + "\n") == {"b.cc": moved}
        assert prep.omitted_reason("b.cc", moved) is None

    def test_a_file_without_a_patch_becomes_a_stub(self, prep, pr_diff):
        """GitHub leaves out the patch of a binary or of text too big to render,
        and reports 0 changes for some of the latter."""
        s = pr_diff.file_section(
            {
                "filename": "fr.lproj/Localizable.strings",
                "status": "modified",
                "changes": 0,
            }
        )
        assert (
            prep.omitted_reason("fr.lproj/Localizable.strings", s)
            == "no patch from GitHub"
        )

    def test_post_review_places_findings_against_the_same_diff(
        self, prep, post, monkeypatch
    ):
        """post-review.py reads the diff again to check each finding's line.
        If it still asked the diff endpoint, every finding on a PR past 300
        files was dropped as "file not in diff" and nothing was posted."""
        a = section("browser/a.cc", ["int x;"])
        patch = a.split("\n", 4)[4]
        self._gh(
            monkeypatch,
            prep,
            [
                {
                    "filename": "browser/a.cc",
                    "status": "modified",
                    "changes": 1,
                    "patch": patch,
                }
            ],
        )
        monkeypatch.setattr(post, "_diff_line_cache", {})
        assert post.fetch_diff_line_ranges("o/r", 1) == {"browser/a.cc": [(1, 3)]}

    def test_a_pr_at_the_files_api_limit_is_refused(self, prep, monkeypatch):
        """Past 3000 the API stops listing, and a review of part of a PR would
        read as a review of all of it."""
        self._gh(
            monkeypatch,
            prep,
            [
                {"filename": f"f{i}", "status": "modified", "patch": "@@ -1 +1 @@\n+x"}
                for i in range(3000)
            ],
        )
        with pytest.raises(RuntimeError, match="limit"):
            prep.fetch_diff(1)


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

    def test_docs_without_a_file_type_read_everything(self, prep):
        for condition in ("always", None):
            assert prep.files_in_scope(condition, self.FILES) == self.FILES

    def test_a_doc_about_one_kind_of_file_reads_only_those(self, prep):
        files = self.FILES + [
            "browser/a_unittest.cc",
            "test/data/page.html",
            "chromium_src/chrome/x.cc",
            "patches/chrome-browser-test-foo.cc.patch",
        ]
        assert prep.files_in_scope("has_build_files", files) == ["browser/BUILD.gn"]
        assert prep.files_in_scope("has_test_files", files) == [
            "browser/a_unittest.cc",
            "test/data/page.html",
        ]
        assert prep.files_in_scope("has_chromium_src", files) == [
            "chromium_src/chrome/x.cc"
        ]

    def test_patches_go_to_the_patch_rules_not_the_language_docs(self, prep):
        """A Chromium upgrade rebases hundreds of .patch files. Each is upstream
        code with Brave's change in it, which only the patch rules judge."""
        files = ["browser/a.cc", "patches/chrome-browser-ui-foo.cc.patch"]
        assert prep.files_in_scope("has_cpp_files", files) == ["browser/a.cc"]
        assert prep.files_in_scope("has_patch_files", files) == [
            "patches/chrome-browser-ui-foo.cc.patch"
        ]
        assert prep.files_in_scope("always", files) == files

    def test_docs_for_flags_drops_unmet_conditions(self, prep):
        docs = [
            {"doc": "cpp.md", "condition": "has_cpp_files"},
            {"doc": "arch.md", "condition": "always"},
        ]
        flags = prep.classify_files(["scripts/x.py"])
        assert [
            d["doc"] for d in prep.docs_for_flags(docs, flags, ["scripts/x.py"])
        ] == ["arch.md"]

    def test_a_paths_doc_runs_only_when_a_file_under_it_changed(self, prep):
        docs = [{"doc": "ui.md", "condition": "paths:ui/,crates/ui-bridge/"}]
        for files, wanted in (
            (["crates/core/src/lib.rs", "uix/a.ts"], []),
            (["crates/core/src/lib.rs", "ui/src/App.tsx"], ["ui.md"]),
            (["crates/ui-bridge/src/lib.rs"], ["ui.md"]),
        ):
            flags = prep.classify_files(files)
            assert [d["doc"] for d in prep.docs_for_flags(docs, flags, files)] == wanted

    def test_a_paths_doc_reads_only_the_files_under_it(self, prep):
        assert prep.files_in_scope("paths:ui/", self.FILES) == ["ui/b.ts"]


class TestParts:
    """A detect subagent reads its prompt in one Read call. Past that it reads
    a page or two and reports the rest clean, so a large diff is split."""

    def test_a_small_diff_is_one_part(self, prep):
        a = section("a.cc", ["int x;"])
        parts = prep._parts(["a.cc"], {"a.cc": a}, {}, 10_000, 1_000)
        assert len(parts) == 1
        assert parts[0][0] == diff_of(a)

    def test_files_are_packed_into_parts_that_fit(self, prep):
        sections = {f"f{i}.cc": section(f"f{i}.cc", ["x" * 900]) for i in range(10)}
        parts = prep._parts(list(sections), sections, {}, 3_000, 1_000)
        assert len(parts) > 1
        assert all(len(d) <= 3_000 for d, _, _ in parts)
        seen = [p for d, _, _ in parts for p in prep.split_diff(d)]
        assert seen == list(sections), "every file once, in diff order"

    def test_the_line_budget_splits_too(self, prep):
        sections = {f"f{i}.cc": section(f"f{i}.cc", ["x"] * 50) for i in range(4)}
        parts = prep._parts(list(sections), sections, {}, 1_000_000, 60)
        assert len(parts) == 4

    def test_a_file_too_big_for_a_part_is_split_at_its_hunks(self, prep):
        hunks = [
            f"@@ -{i * 100},1 +{i * 100},2 @@\n ctx\n+" + "y" * 900 for i in range(1, 6)
        ]
        big = "diff --git a/big.cc b/big.cc\n--- a/big.cc\n+++ b/big.cc\n" + "\n".join(
            hunks
        )
        parts = prep._parts(["big.cc"], {"big.cc": big}, {}, 2_500, 1_000)
        assert len(parts) > 1
        for diff_text, _, ranges in parts:
            assert diff_text.startswith("diff --git a/big.cc b/big.cc")
            assert list(ranges) == ["big.cc"]
        all_ranges = [r for _, _, ranges in parts for r in ranges["big.cc"]]
        assert all_ranges == prep.parse_diff_line_ranges(big)["big.cc"]

    def test_stubs_ride_along_without_content(self, prep):
        lock = section("package-lock.json", ["{}"])
        parts = prep._parts(
            ["package-lock.json"],
            {"package-lock.json": lock},
            {"package-lock.json": "lockfile"},
            10_000,
            1_000,
        )
        assert parts[0][0] == "" and "lockfile" in parts[0][1][0]

    def test_a_split_prompt_says_which_part_it_is(self, prep):
        assert prep._part_note(0, 1) is None
        assert "part 2 of 3" in prep._part_note(1, 3)


class TestDiscoverBestPractices:
    def run(self, tmp_path, *flags):
        result = subprocess.run(
            [
                sys.executable,
                os.path.join(SKILL_DIR, "discover-best-practices.py"),
                str(tmp_path),
                *flags,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return {d["doc"]: d["condition"] for d in json.loads(result.stdout)}

    def test_a_paths_condition_survives_the_flag_filter_with_its_case(self, tmp_path):
        (tmp_path / "ui.md").write_text("# UI\n\n<!-- applicability: Paths:UI/ -->\n")
        (tmp_path / "cpp.md").write_text("<!-- applicability: has_cpp_files -->\n")
        assert self.run(tmp_path, "--has-frontend") == {"ui.md": "paths:UI/"}


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
        monkeypatch.setattr(prep, "fetch_pr_body", lambda n: "The author's words.")
        monkeypatch.setattr(prep, "REVIEW_SUMMARY", False)
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

    def test_a_diff_past_one_read_goes_into_pages_the_prompt_names(
        self, prep, stubbed, tmp_dir, monkeypatch
    ):
        """On brave-core #39947 the subagents read 6-25% of 1.8MB prompts and
        reported the rest clean. A prompt now fits one read, the diff is in
        pages that each fit one read, and every file is in some page."""
        monkeypatch.setattr(prep, "MAX_PROMPT_CHARS", 1_500)
        monkeypatch.setattr(prep, "PAGES_PER_PART", 2)
        files = [
            section(f"browser/f{i}.cc", [f"int v{i} = {'x' * 400};"]) for i in range(8)
        ]
        stubbed["text"] = diff_of(*files)
        monkeypatch.setattr(
            prep,
            "fetch_prior_comments",
            lambda *a, **k: ("Earlier: please rename v0.", True),
        )
        result, error = self._run(prep, tmp_dir)
        assert error is None
        correctness = [
            p for p in result["subagent_prompts"] if p["kind"] == "correctness"
        ]
        assert len(correctness) > 1, "eight files over a 1.5K budget is one part"
        assert [p["part"] for p in correctness] == list(range(1, len(correctness) + 1))
        seen = []
        for entry in correctness:
            with open(entry["prompt_file"]) as f:
                prompt = f.read()
            assert "+int v" not in prompt, "the diff belongs in the pages"
            assert f"part {entry['part']} of {len(correctness)}" in prompt
            for path in entry["read_files"]:
                assert f"- {path}" in prompt
                with open(path) as f:
                    page = f.read()
                assert len(page) <= 1_500
                seen += list(prep.split_diff(page))
            assert "please rename v0" in open(entry["read_files"][0]).read()
        assert seen == [f"browser/f{i}.cc" for i in range(8)]

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

    def test_guidance_adds_a_project_prompt_and_only_then(
        self, prep, stubbed, tmp_dir, monkeypatch
    ):
        first, _ = self._run(prep, tmp_dir)
        assert "project" not in [p["kind"] for p in first["subagent_prompts"]]

        monkeypatch.setattr(
            prep, "REVIEW_GUIDANCE", ["Check the spec.", "Check tests."]
        )
        result, _ = self._run(prep, tmp_dir)
        assert [p["chunk_id"] for p in result["subagent_prompts"]][-2:] == [
            "correctness",
            "project",
        ]
        with open(result["subagent_prompts"][-1]["prompt_file"]) as f:
            prompt = f.read()
        assert "- Check the spec.\n- Check tests." in prompt
        assert "+int x;" in prompt and "+let y = 1" in prompt
        assert "PR head is at: " in prompt
        assert "project_candidates.json" in prompt
        with open(result["subagent_prompts"][-2]["prompt_file"]) as f:
            assert "Check the spec." not in f.read()

    def test_a_summary_prompt_covers_the_whole_pr_and_only_when_asked(
        self, prep, stubbed, tmp_dir, monkeypatch
    ):
        first, _ = self._run(prep, tmp_dir)
        assert first["summary_prompt"] is None

        monkeypatch.setattr(prep, "REVIEW_SUMMARY", True)
        prior = self._hashes(prep)
        stubbed["text"] = diff_of(self.CC, section("ui/b.ts", ["let y = 2"]), self.LOCK)
        result, _ = self._run(prep, tmp_dir, prior=prior)
        assert [p["kind"] for p in result["subagent_prompts"]] == [
            "rules",
            "correctness",
        ]
        summary = result["summary_prompt"]
        with open(summary["prompt_file"]) as f:
            prompt = f.read()
        assert "The author's words." in prompt
        assert "+int x;" in prompt and "+let y = 2" in prompt
        assert "the other 2 were reviewed already" not in prompt
        assert summary["results_file"].endswith("summary.json")
        assert "Never @-mention anyone." in prompt
        with open(summary["body_file"]) as f:
            assert f.read() == "The author's words."

    def test_the_validator_sees_the_guidance_it_judges_against(self, prep):
        ctx = {"number": 1, "title": "t", "bot_username": "bot"}
        args = ([{"id": "c1"}], [], "", {}, [], "/src", "/out.json")
        plain = prep.build_validate_prompt(ctx, *args)
        assert "Project review guidance" not in plain
        assert "project review guidance" not in plain
        guided = prep.build_validate_prompt(
            dict(ctx, guidance=["Check the spec."]), *args
        )
        assert "- Check the spec." in guided
        assert "applies the project review guidance" in guided


class TestProfileReview:
    def test_guidance_is_a_string_or_a_list_of_text(self, prep):
        assert prep.review_guidance({}) == []
        assert prep.review_guidance({"review": {"guidance": " one "}}) == ["one"]
        assert prep.review_guidance(
            {"review": {"guidance": ["a", "", " ", 3, "b"]}}
        ) == [
            "a",
            "b",
        ]

    @pytest.mark.parametrize(
        "review", ["text", ["a"], {"guidance": 5}, {"guidance": {}}]
    )
    def test_malformed_guidance_means_none(self, prep, review):
        assert prep.review_guidance({"review": review}) == []

    def test_verdict_needs_a_json_true(self, post):
        assert post.review_verdict({"review": {"verdict": True}}) is True
        for value in ("true", 1, None):
            assert post.review_verdict({"review": {"verdict": value}}) is False
        assert post.review_verdict({}) is False

    def test_bravebot_asks_for_a_review_and_brave_core_does_not(self, prep, post):
        for name in ("bravebot", "brave-core"):
            profile = prep.load_profile({"project": {"profile": name}}, ROOT_DIR)
            asked = name == "bravebot"
            assert bool(prep.review_guidance(profile)) is asked, name
            assert post.review_verdict(profile) is asked, name


class TestVerdict:
    PR = {"number": 7, "title": "t", "headRefOid": "abcdef1234567890"}
    FINDING = {
        "file": "a.rs",
        "line": 3,
        "severity": "high",
        "rule": "Contradicts a spec clause",
        "draft_comment": "The spec says otherwise.",
    }

    @pytest.fixture
    def sent(self, post, monkeypatch):
        sent = {}
        monkeypatch.setattr(post, "update_cache", lambda *a, **k: None)
        monkeypatch.setattr(post, "fetch_existing_comments", lambda r, n: [])
        monkeypatch.setattr(post, "check_can_approve", lambda n, b: True)
        monkeypatch.setattr(
            post,
            "submit_approval",
            lambda repo, n, body="": sent.update(approve=body) or "url",
        )
        monkeypatch.setattr(
            post,
            "post_batch_review",
            lambda repo, n, vs, sha, body="": sent.update(comment=body) or ("url", 1),
        )
        return sent

    def _run(self, post, violations):
        return post.process_pr(dict(self.PR, violations=violations), "o/r", "bot", True)

    def test_an_approval_opens_with_the_recommendation(self, post, sent, monkeypatch):
        monkeypatch.setattr(post, "VERDICT", True)
        assert self._run(post, [])["status"] == "approved"
        assert sent == {"approve": "**Recommendation: approve**"}

    def test_a_review_with_comments_recommends_changes(self, post, sent, monkeypatch):
        monkeypatch.setattr(post, "VERDICT", True)
        assert self._run(post, [dict(self.FINDING)])["status"] == "posted"
        assert sent == {"comment": "**Recommendation: request changes**"}

    def test_each_comment_opens_with_its_severity(self, post, monkeypatch):
        """The author should tell a bug from a style point before reading on."""
        sent = {}
        monkeypatch.setattr(post, "update_cache", lambda *a, **k: None)
        monkeypatch.setattr(post, "fetch_existing_comments", lambda r, n: [])
        monkeypatch.setattr(
            post,
            "post_batch_review",
            lambda repo, n, vs, sha, body="": sent.update(vs=vs) or ("url", len(vs)),
        )
        medium = dict(
            self.FINDING,
            file="b.rs",
            severity="medium",
            rule="Another rule",
            rule_link=f"{LINK}#CS-001",
        )
        self._run(post, [dict(self.FINDING), medium])
        bodies = {v["file"]: v["draft_comment"] for v in sent["vs"]}
        assert bodies["a.rs"] == "**Severity: High**\n\nThe spec says otherwise."
        assert bodies["b.rs"].startswith("**Severity: Medium**\n\n")

    def test_a_labelled_comment_is_not_labelled_twice(self, post):
        v = dict(self.FINDING)
        post.label_severity(v)
        post.label_severity(v)
        assert v["draft_comment"].count("**Severity:") == 1

    def test_the_how_section_follows_the_recommendation(self, post, sent, monkeypatch):
        monkeypatch.setattr(post, "VERDICT", True)
        pr = dict(self.PR, violations=[], checks_details="<details>x</details>")
        post.process_pr(pr, "o/r", "bot", True)
        pr = dict(
            self.PR,
            violations=[dict(self.FINDING)],
            checks_details="<details>y</details>",
        )
        post.process_pr(pr, "o/r", "bot", True)
        assert sent == {
            "approve": "**Recommendation: approve**\n\n<details>x</details>",
            "comment": "**Recommendation: request changes**\n\n<details>y</details>",
        }

    def test_the_description_comes_before_the_how_section(
        self, post, sent, monkeypatch
    ):
        monkeypatch.setattr(post, "VERDICT", True)
        pr = dict(
            self.PR,
            violations=[],
            description_details="**What this pull request does**",
            checks_details="<details>x</details>",
        )
        post.process_pr(pr, "o/r", "bot", True)
        assert sent["approve"] == (
            "**Recommendation: approve**\n\n**What this pull request does**"
            "\n\n<details>x</details>"
        )

    def test_no_verdict_keeps_the_bodies_empty(self, post, sent, monkeypatch):
        monkeypatch.setattr(post, "VERDICT", False)
        for extra in ({}, {"checks_details": "<details>x</details>"}):
            for found in ([], [dict(self.FINDING)]):
                post.process_pr(
                    dict(self.PR, violations=found, description_details="d", **extra),
                    "o/r",
                    "bot",
                    True,
                )
        assert sent == {"approve": "", "comment": ""}


class TestCleanReviewOfTheBotsOwnPr:
    """GitHub refuses an approval from a PR's author. A clean review of a PR
    the bot opened used to try one anyway, and the refusal left the PR with
    nothing on it."""

    PR = {"number": 7, "title": "t", "headRefOid": "abcdef1234567890"}

    @pytest.fixture
    def posted(self, post, monkeypatch):
        reviews = []
        monkeypatch.setattr(post, "update_cache", lambda *a, **k: None)
        monkeypatch.setattr(post, "fetch_existing_comments", lambda r, n: [])
        monkeypatch.setattr(post, "check_can_approve", lambda n, b: True)
        monkeypatch.setattr(
            post,
            "submit_approval",
            lambda repo, n, body="": reviews.append(("approve", n, body)) or "url",
        )
        monkeypatch.setattr(
            post,
            "submit_comment_review",
            lambda repo, n, sha, body: reviews.append(("comment", n, body)) or "url",
        )
        return reviews

    def test_its_own_pr_gets_a_comment_not_an_approval(self, post, posted, monkeypatch):
        monkeypatch.setattr(post, "VERDICT", True)
        pr = dict(self.PR, author="bot", checks_details="<details>x</details>")
        result = post.process_pr(pr, "o/r", "bot", True)
        assert [(kind, n) for kind, n, _ in posted] == [("comment", 7)]
        body = posted[0][2]
        assert body.startswith("**Recommendation: approve**")
        assert "No issues found at abcdef12" in body
        assert body.endswith("<details>x</details>")
        assert result["status"] == "commented" and result["review_url"] == "url"

    def test_without_a_verdict_the_comment_still_says_it_was_clean(
        self, post, posted, monkeypatch
    ):
        monkeypatch.setattr(post, "VERDICT", False)
        post.process_pr(dict(self.PR, author="bot"), "o/r", "bot", True)
        assert posted[0][2].startswith("No issues found at abcdef12")

    def test_someone_elses_pr_is_still_approved(self, post, posted):
        post.process_pr(dict(self.PR, author="alice"), "o/r", "bot", True)
        assert [kind for kind, _, _ in posted] == ["approve"]


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
        n = 2 * post.MAX_COMMENTS_PER_PR + 2
        vs = [{"file": f"f{i}", "line": 1, "severity": "medium"} for i in range(n)]
        kept, _ = post.prioritize_violations(list(vs), False)
        assert len(kept) == post.MAX_COMMENTS_PER_PR
        wider = post.MAX_COMMENTS_PER_PR * 2
        kept, _ = post.prioritize_violations(list(vs), False, limit=wider)
        assert len(kept) == wider

    def test_an_approved_pr_gets_high_and_medium_but_no_nits(self, post):
        """Someone already said yes, so nits are noise; a substantive violation
        is still worth saying before it merges."""
        vs = [
            {"file": "a", "line": 1, "severity": "low"},
            {"file": "b", "line": 1, "severity": "medium"},
            {"file": "c", "line": 1, "severity": "high"},
        ]
        kept, dropped = post.prioritize_violations(list(vs), True)
        assert [v["severity"] for v in kept] == ["high", "medium"]
        assert dropped == 1

    def test_an_approved_pr_still_has_the_cap(self, post):
        n = post.MAX_COMMENTS_PER_PR + 3
        vs = [{"file": f"f{i}", "line": 1, "severity": "medium"} for i in range(n)]
        kept, dropped = post.prioritize_violations(list(vs), True)
        assert len(kept) == post.MAX_COMMENTS_PER_PR
        assert dropped == 3

    def test_the_detect_prompt_asks_an_approved_pr_for_medium_too(self, prep):
        header = "\n".join(
            prep._prompt_header({"number": 1, "title": "t", "has_approval": True})
        )
        assert "high- and medium-severity" in header and "no low-severity" in header


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
            for i in range(sel.CANDIDATE_LIMIT + 5)
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
        assert by_number[1]["detected"] == 1 and by_number[2]["detected"] == 0
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

    def test_the_author_reaches_post_review(self, collect, tmp_dir):
        """post-review needs it to tell the bot's own PR, which it may not
        approve, from anyone else's."""
        manifest = {"prs": [dict(self._pr(tmp_dir, validation=None), author="bot")]}
        [result] = collect.build_post_review_input(manifest)["pr_results"]
        assert result["author"] == "bot"


class TestDescribePr:
    FULL = (
        "Closes o/r#1\n\nUser impact: none -- a refactor.\n\n## The problem\nIt broke.\n\n"
        "## Reproduce\n1. Run it.\n\n## The fix\nFixed.\n\n## Test plan\n- [x] make check\n"
    )

    def _pr(self, tmp_dir, result=None, body=FULL):
        summary = {
            "results_file": os.path.join(tmp_dir, "summary.json"),
            "body_file": os.path.join(tmp_dir, "pr_body.md"),
        }
        if result is not None:
            with open(summary["results_file"], "w") as f:
                json.dump(result, f)
        with open(summary["body_file"], "w") as f:
            f.write(body)
        return {"summary_prompt": summary}

    def test_a_complete_description_has_nothing_missing(self, collect):
        assert collect.missing_from_description(self.FULL) == []

    def test_the_missing_parts_are_named(self, collect):
        body = "Closes o/r#1\n\n## Summary\nIt broke.\n\n## Reproduce\nTry it.\n"
        missing = collect.missing_from_description(body)
        assert len(missing) == 4
        assert missing[0].startswith("A `User impact:` line")
        assert "A `## The fix` section." in missing
        assert "A `## Test plan` section." in missing
        assert any("Steps in `## Reproduce`" in m for m in missing)
        assert collect.missing_from_description("  ") == ["A description: it is empty."]

    def test_a_pr_without_a_summary_prompt_gets_nothing(self, collect):
        assert collect.describe_pr({"summary_prompt": None}) == ""
        assert collect.describe_pr({}) == ""

    def test_description_steps_and_gaps_are_laid_out(self, collect, tmp_dir):
        pr = self._pr(
            tmp_dir,
            {
                "description": "It retries a slow reply.",
                "manual_testing": {
                    "in_description": False,
                    "steps": ["Run it.", "Wait."],
                },
            },
            body="Just a title.",
        )
        text = collect.describe_pr(pr)
        assert text.startswith("**What this pull request does**\n\nIt retries")
        assert "**Trying it by hand**" in text
        assert "1. Run it.\n2. Wait." in text
        assert "**Missing from the description**\n\n- A `User impact:` line" in text

    def test_steps_are_left_out_when_the_description_has_them(self, collect, tmp_dir):
        pr = self._pr(
            tmp_dir,
            {
                "description": "d",
                "manual_testing": {"in_description": True, "steps": ["Run it."]},
            },
        )
        text = collect.describe_pr(pr)
        assert "Trying it by hand" not in text
        assert "Missing from the description" not in text

    def test_an_unreadable_or_malformed_result_still_lists_the_gaps(
        self, collect, tmp_dir
    ):
        pr = self._pr(tmp_dir, None, body="")
        assert collect.describe_pr(pr).startswith("**Missing from the description**")
        pr = self._pr(tmp_dir, ["not", "a", "dict"], body=self.FULL)
        assert collect.describe_pr(pr) == ""
        pr = self._pr(
            tmp_dir,
            {"description": 5, "manual_testing": {"steps": "run it"}},
            body=self.FULL,
        )
        assert collect.describe_pr(pr) == ""

    def test_the_section_reaches_the_post_review_input(self, collect, tmp_dir):
        pr = dict(
            self._pr(tmp_dir, {"description": "It does a thing."}),
            number=1,
            title="t",
            subagent_prompts=[],
            validation=None,
        )
        [result] = collect.build_post_review_input({"prs": [pr]})["pr_results"]
        assert "It does a thing." in result["description_details"]


class TestDescribeChecks:
    def _pr(self, tmp_dir, kinds, **extra):
        prompts = []
        for i, (kind, doc, rules) in enumerate(kinds):
            path = os.path.join(tmp_dir, f"r{i}.json")
            with open(path, "w") as f:
                f.write("{}")
            entry = {"kind": kind, "results_file": path}
            if doc:
                entry.update(doc=doc, rule_count=rules)
            prompts.append(entry)
        return dict({"subagent_prompts": prompts, "files_total": 3}, **extra)

    KINDS = [
        ("rules", "specs.md", 10),
        ("rules", "tests.md", 5),
        ("correctness", None, 0),
        ("project", None, 0),
    ]

    def test_a_clean_review_says_what_it_read_and_that_nothing_was_flagged(
        self, collect, tmp_dir
    ):
        text = collect.describe_checks(
            self._pr(tmp_dir, self.KINDS, files_reviewed=3),
            [],
            ["Whether the tests can fail."],
        )
        assert text.startswith("<details>\n<summary>How this review reached")
        assert text.endswith("</details>")
        assert "read 3 changed files." in text
        assert (
            "15 rules from this project's best-practice documents (specs, tests)"
            in text
        )
        assert "**Bugs.**" in text
        assert "  - Whether the tests can fail." in text
        assert "nothing to double-check" in text

    def test_a_chunk_split_into_parts_counts_its_rules_once(self, collect, tmp_dir):
        """Three parts of the diff checked against the same 10 rules is still 10
        rules, not 30."""
        pr = self._pr(tmp_dir, [("rules", "specs.md", 10)] * 3, files_reviewed=3)
        for entry in pr["subagent_prompts"]:
            entry["chunk_index"] = 0
        assert "compared with 10 rules" in collect.describe_checks(pr, [])

    def test_a_re_review_says_it_read_only_the_changed_files(self, collect, tmp_dir):
        text = collect.describe_checks(
            self._pr(tmp_dir, self.KINDS, files_reviewed=1), []
        )
        assert "read 1 of the 3 changed files" in text
        assert "changed since the bot last reviewed" in text

    def test_flagged_problems_are_counted_through_the_second_read(
        self, collect, tmp_dir
    ):
        pr = self._pr(
            tmp_dir,
            self.KINDS,
            files_reviewed=3,
            detected=4,
            validation={"candidates": 3},
        )
        assert "flagged 4 possible problems. 3 of them went to a second reader" in (
            collect.describe_checks(pr, [{"file": "a"}])
        )
        assert "and kept 1." in collect.describe_checks(pr, [{"file": "a"}])
        assert "none of them to be a real problem" in collect.describe_checks(pr, [])

    def test_flags_that_only_repeat_need_no_second_look(self, collect, tmp_dir):
        pr = self._pr(tmp_dir, self.KINDS, files_reviewed=3, detected=2)
        assert "none needed a second look" in collect.describe_checks(pr, [])

    def test_a_check_that_wrote_nothing_is_not_claimed(self, collect, tmp_dir):
        pr = self._pr(tmp_dir, self.KINDS, files_reviewed=3)
        os.remove(pr["subagent_prompts"][2]["results_file"])
        text = collect.describe_checks(pr, [])
        assert "**Bugs.**" not in text
        assert "1 of 4 checks produced no result" in text

    def test_a_project_without_guidance_has_no_criteria_item(self, collect, tmp_dir):
        pr = self._pr(tmp_dir, self.KINDS[:3], files_reviewed=3)
        assert "own criteria" not in collect.describe_checks(pr, [])

    def test_the_section_reaches_the_post_review_input(self, collect, tmp_dir):
        manifest = {
            "prs": [
                dict(
                    self._pr(tmp_dir, self.KINDS, files_reviewed=3),
                    number=1,
                    title="t",
                    validation=None,
                )
            ]
        }
        [result] = collect.build_post_review_input(manifest, ["Check it."])[
            "pr_results"
        ]
        assert "  - Check it." in result["checks_details"]

    def test_checks_come_from_the_profile_as_text(self, prep, collect):
        assert collect.review_checks({}) == []
        assert collect.review_checks({"review": {"checks": " a "}}) == []
        assert collect.review_checks({"review": {"checks": ["a", "", 3, " b "]}}) == [
            "a",
            "b",
        ]
        profile = prep.load_profile({"project": {"profile": "bravebot"}}, ROOT_DIR)
        assert collect.review_checks(profile)
        assert (
            collect.review_checks(
                prep.load_profile({"project": {"profile": "brave-core"}}, ROOT_DIR)
            )
            == []
        )


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


class TestCleanReviewTheGateRefuses:
    """A clean review the approval gate will not turn into an approval (the bot
    already approved this commit, or its earlier threads are open). GitHub
    clears a review request only when a review is submitted, so posting nothing
    leaves the PR in the review-request queue and every poll reviews it again."""

    PR = {"number": 7, "title": "t", "headRefOid": "abcdef1234567890"}

    @pytest.fixture
    def posted(self, post, monkeypatch):
        reviews = []
        monkeypatch.setattr(post, "update_cache", lambda *a, **k: None)
        monkeypatch.setattr(post, "fetch_existing_comments", lambda r, n: [])
        monkeypatch.setattr(post, "check_can_approve", lambda n, b: False)
        monkeypatch.setattr(
            post,
            "submit_approval",
            lambda *a: pytest.fail("the gate refused; nothing may approve"),
        )
        monkeypatch.setattr(
            post,
            "submit_comment_review",
            lambda repo, n, sha, body: reviews.append((n, sha, body)) or "url",
        )
        return reviews

    def test_a_standing_request_is_answered(self, post, posted, monkeypatch):
        monkeypatch.setattr(post, "bot_review_requested", lambda r, n, b: True)
        result = post.process_pr(dict(self.PR), "o/r", "bot", True)
        assert [(n, sha) for n, sha, _ in posted] == [(7, "abcdef1234567890")]
        assert "no new issues" in posted[0][2]
        assert result["status"] == "approved" and result["review_url"] == "url"

    def test_an_unrequested_pr_gets_nothing(self, post, posted, monkeypatch):
        """The sweep reviews PRs nobody asked the bot about. A comment there
        would be noise on every rebase."""
        monkeypatch.setattr(post, "bot_review_requested", lambda r, n, b: False)
        post.process_pr(dict(self.PR), "o/r", "bot", True)
        assert posted == []
