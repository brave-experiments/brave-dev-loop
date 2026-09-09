"""Tests for chunk-best-practices.py.

Covers: split_into_rules, chunk_rules, process_doc, and CLI output.
"""

import json
import os
import subprocess
import sys
import textwrap

import pytest

SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__),
    os.pardir,
    ".claude",
    "skills",
    "review-prs",
    "chunk-best-practices.py",
)

# Path to real best-practices docs — derived from config if available.
_BOT_DIR = os.path.join(os.path.dirname(__file__), os.pardir)
_CONFIG_PATH = os.path.join(_BOT_DIR, "config.json")
_BP_DOCS_DIR = None
if os.path.isfile(_CONFIG_PATH):
    import json as _json

    with open(_CONFIG_PATH) as _f:
        _bp = _json.load(_f).get("bestPractices", {})
        _BP_DOCS_DIR = _bp.get("docsDir")
if not _BP_DOCS_DIR:
    _BP_DOCS_DIR = os.environ.get("BP_DOCS_DIR", "")
BP_DIR = os.path.join(_BOT_DIR, _BP_DOCS_DIR, "best-practices") if _BP_DOCS_DIR else ""


# ── Helpers ──────────────────────────────────────────────────────────────────


def make_doc(num_rules, prefix="R", title="# Test Doc", include_subsections=False):
    """Build a synthetic best-practice document with N ## rules."""
    parts = [title, ""]
    for i in range(1, num_rules + 1):
        parts.append(f'<a id="{prefix}-{i:03d}"></a>')
        parts.append("")
        parts.append(f"## Rule {prefix}-{i:03d}")
        parts.append("")
        parts.append(f"Description for rule {prefix}-{i:03d}.")
        parts.append("")
        if include_subsections and i % 3 == 0:
            # Add ### sub-headings to every 3rd rule.
            parts.append(f'<a id="{prefix}-{i:03d}a"></a>')
            parts.append("")
            parts.append(f"### Sub-rule {prefix}-{i:03d}a")
            parts.append("")
            parts.append("Sub-rule detail.")
            parts.append("")
        parts.append("---")
        parts.append("")
    return "\n".join(parts)


def write_doc(tmp_dir, content, name="test-bp.md"):
    path = os.path.join(tmp_dir, name)
    with open(path, "w") as f:
        f.write(content)
    return path


# ═══════════════════════════════════════════════════════════════════════════
# split_into_rules
# ═══════════════════════════════════════════════════════════════════════════


class TestSplitIntoRules:
    def test_basic_split(self, chunk_best_practices):
        doc = make_doc(3)
        header, rules = chunk_best_practices.split_into_rules(doc)
        assert "# Test Doc" in header
        assert len(rules) == 3

    def test_heading_text_extraction(self, chunk_best_practices):
        doc = make_doc(2, prefix="CS")
        _, rules = chunk_best_practices.split_into_rules(doc)
        assert rules[0]["heading"] == "Rule CS-001"
        assert rules[1]["heading"] == "Rule CS-002"

    def test_emoji_stripped_from_heading(self, chunk_best_practices):
        doc = textwrap.dedent("""\
            # Doc

            <a id="X-001"></a>

            ## ✅ Use Good Names

            Details.
        """)
        _, rules = chunk_best_practices.split_into_rules(doc)
        assert rules[0]["heading"] == "Use Good Names"

    def test_subsections_kept_with_parent(self, chunk_best_practices):
        doc = textwrap.dedent("""\
            # Doc

            <a id="X-001"></a>

            ## Parent Rule

            <a id="X-002"></a>

            ### Sub Rule A

            Sub detail A.

            <a id="X-003"></a>

            ### Sub Rule B

            Sub detail B.

            ---

            <a id="X-004"></a>

            ## Next Top Rule

            Details.
        """)
        _, rules = chunk_best_practices.split_into_rules(doc)
        # ### headings should NOT cause additional splits.
        assert len(rules) == 2
        assert "Sub Rule A" in rules[0]["text"]
        assert "Sub Rule B" in rules[0]["text"]
        assert rules[1]["heading"] == "Next Top Rule"

    def test_no_rules_returns_full_text(self, chunk_best_practices):
        doc = "# Just a title\n\nSome text without rules.\n"
        header, rules = chunk_best_practices.split_into_rules(doc)
        assert rules == []
        assert "Just a title" in header

    def test_rule_text_contains_content(self, chunk_best_practices):
        doc = make_doc(2)
        _, rules = chunk_best_practices.split_into_rules(doc)
        assert "Description for rule R-001" in rules[0]["text"]
        assert "Description for rule R-002" in rules[1]["text"]

    def test_rules_dont_overlap(self, chunk_best_practices):
        doc = make_doc(5)
        _, rules = chunk_best_practices.split_into_rules(doc)
        for i, rule in enumerate(rules):
            # Each rule should only contain its own description.
            for j in range(len(rules)):
                if j != i:
                    assert f"Description for rule R-{j + 1:03d}" not in rule["text"]


# ═══════════════════════════════════════════════════════════════════════════
# chunk_rules
# ═══════════════════════════════════════════════════════════════════════════


class TestChunkRules:
    def test_small_list_single_chunk(self, chunk_best_practices):
        rules = [{"text": f"r{i}", "heading": f"R{i}"} for i in range(5)]
        chunks = chunk_best_practices.chunk_rules(rules, chunk_size=20)
        assert len(chunks) == 1
        assert len(chunks[0]) == 5

    def test_exact_chunk_size_single_chunk(self, chunk_best_practices):
        rules = [{"text": f"r{i}", "heading": f"R{i}"} for i in range(20)]
        chunks = chunk_best_practices.chunk_rules(rules, chunk_size=20)
        assert len(chunks) == 1

    def test_even_distribution(self, chunk_best_practices):
        """47 rules at chunk_size=20 should give 2 chunks of 24+23."""
        rules = [{"text": f"r{i}", "heading": f"R{i}"} for i in range(47)]
        chunks = chunk_best_practices.chunk_rules(rules, chunk_size=20)
        assert len(chunks) == 2
        sizes = [len(c) for c in chunks]
        assert sizes == [24, 23]

    def test_large_doc_distribution(self, chunk_best_practices):
        """78 rules at chunk_size=20 should give 4 chunks of 20+20+19+19."""
        rules = [{"text": f"r{i}", "heading": f"R{i}"} for i in range(78)]
        chunks = chunk_best_practices.chunk_rules(rules, chunk_size=20)
        assert len(chunks) == 4
        sizes = [len(c) for c in chunks]
        assert sizes == [20, 20, 19, 19]

    def test_65_rules_distribution(self, chunk_best_practices):
        """65 rules at chunk_size=20 should give 3 chunks of 22+22+21."""
        rules = [{"text": f"r{i}", "heading": f"R{i}"} for i in range(65)]
        chunks = chunk_best_practices.chunk_rules(rules, chunk_size=20)
        assert len(chunks) == 3
        sizes = [len(c) for c in chunks]
        assert sizes == [22, 22, 21]

    def test_all_rules_preserved(self, chunk_best_practices):
        """All rules must appear in exactly one chunk."""
        rules = [{"text": f"r{i}", "heading": f"R{i}"} for i in range(58)]
        chunks = chunk_best_practices.chunk_rules(rules, chunk_size=20)
        flat = [r for chunk in chunks for r in chunk]
        assert len(flat) == 58
        assert flat == rules

    def test_no_empty_chunks(self, chunk_best_practices):
        for n in range(1, 100):
            rules = [{"text": f"r{i}", "heading": f"R{i}"} for i in range(n)]
            chunks = chunk_best_practices.chunk_rules(rules, chunk_size=20)
            for chunk in chunks:
                assert len(chunk) > 0

    def test_custom_chunk_size(self, chunk_best_practices):
        rules = [{"text": f"r{i}", "heading": f"R{i}"} for i in range(30)]
        chunks = chunk_best_practices.chunk_rules(rules, chunk_size=10)
        assert len(chunks) == 3
        assert all(len(c) == 10 for c in chunks)


# ═══════════════════════════════════════════════════════════════════════════
# process_doc (integration)
# ═══════════════════════════════════════════════════════════════════════════


class TestProcessDoc:
    def test_small_doc_single_chunk(self, chunk_best_practices, tmp_dir):
        path = write_doc(tmp_dir, make_doc(5))
        result = chunk_best_practices.process_doc(path)
        assert len(result) == 1
        assert result[0]["rule_count"] == 5
        assert result[0]["chunk_index"] == 0
        assert result[0]["total_chunks"] == 1

    def test_large_doc_multiple_chunks(self, chunk_best_practices, tmp_dir):
        path = write_doc(tmp_dir, make_doc(50))
        result = chunk_best_practices.process_doc(path, chunk_size=20)
        # round(50/20) = round(2.5) = 2 (banker's rounding) -> 25+25
        assert len(result) == 2
        assert sum(c["rule_count"] for c in result) == 50

    def test_header_in_every_chunk(self, chunk_best_practices, tmp_dir):
        path = write_doc(tmp_dir, make_doc(40))
        result = chunk_best_practices.process_doc(path, chunk_size=20)
        for chunk in result:
            assert chunk["content"].startswith("# Test Doc")

    def test_headings_match_rules(self, chunk_best_practices, tmp_dir):
        path = write_doc(tmp_dir, make_doc(25))
        result = chunk_best_practices.process_doc(path, chunk_size=20)
        assert len(result) == 1  # round(25/20) = 1
        assert len(result[0]["headings"]) == 25

    def test_doc_name_in_output(self, chunk_best_practices, tmp_dir):
        path = write_doc(tmp_dir, make_doc(3), name="coding-standards.md")
        result = chunk_best_practices.process_doc(path)
        assert result[0]["doc"] == "coding-standards.md"

    def test_no_rules_doc(self, chunk_best_practices, tmp_dir):
        path = write_doc(tmp_dir, "# Empty Doc\n\nNo rules here.\n")
        result = chunk_best_practices.process_doc(path)
        assert len(result) == 1
        assert result[0]["rule_count"] == 0

    def test_subsections_not_split(self, chunk_best_practices, tmp_dir):
        path = write_doc(tmp_dir, make_doc(10, include_subsections=True))
        result = chunk_best_practices.process_doc(path)
        # Only ## headings count as rules, ### stays with parent.
        assert result[0]["rule_count"] == 10

    def test_chunk_index_sequential(self, chunk_best_practices, tmp_dir):
        path = write_doc(tmp_dir, make_doc(60))
        result = chunk_best_practices.process_doc(path, chunk_size=20)
        for i, chunk in enumerate(result):
            assert chunk["chunk_index"] == i
            assert chunk["total_chunks"] == len(result)


# ═══════════════════════════════════════════════════════════════════════════
# Real docs (only if best-practices dir is configured and present)
# ═══════════════════════════════════════════════════════════════════════════

HAS_REAL_DOCS = bool(BP_DIR) and os.path.isdir(BP_DIR)


@pytest.mark.skipif(not HAS_REAL_DOCS, reason="best-practices docs not present")
class TestRealDocs:
    """Smoke tests against actual best-practice documents."""

    def _all_docs(self):
        return sorted(
            os.path.join(BP_DIR, f) for f in os.listdir(BP_DIR) if f.endswith(".md")
        )

    def test_all_docs_parse_without_error(self, chunk_best_practices):
        for doc_path in self._all_docs():
            result = chunk_best_practices.process_doc(doc_path)
            assert len(result) >= 1, f"Failed to process {doc_path}"
            total = sum(c["rule_count"] for c in result)
            assert total > 0, f"No rules found in {doc_path}"

    def test_no_chunk_exceeds_max(self, chunk_best_practices):
        """No chunk should have more than ~28 rules (chunk_size + rounding)."""
        for doc_path in self._all_docs():
            result = chunk_best_practices.process_doc(doc_path, chunk_size=20)
            for chunk in result:
                assert chunk["rule_count"] <= 28, (
                    f"{chunk['doc']} chunk {chunk['chunk_index']} has "
                    f"{chunk['rule_count']} rules"
                )

    def test_total_rules_preserved(self, chunk_best_practices):
        """Chunking must not lose or duplicate rules."""
        for doc_path in self._all_docs():
            text = open(doc_path).read()
            _, rules = chunk_best_practices.split_into_rules(text)
            result = chunk_best_practices.process_doc(doc_path)
            total = sum(c["rule_count"] for c in result)
            assert total == len(rules), (
                f"{os.path.basename(doc_path)}: "
                f"split found {len(rules)} rules but chunks have {total}"
            )

    def test_headings_not_empty(self, chunk_best_practices):
        for doc_path in self._all_docs():
            result = chunk_best_practices.process_doc(doc_path)
            for chunk in result:
                for heading in chunk["headings"]:
                    assert heading.strip(), (
                        f"Empty heading in {chunk['doc']} chunk {chunk['chunk_index']}"
                    )


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════


class TestCLI:
    def test_outputs_valid_json(self, tmp_dir):
        path = write_doc(tmp_dir, make_doc(5))
        result = subprocess.run(
            [sys.executable, SCRIPT_PATH, path],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert len(data) == 1
        assert data[0]["rule_count"] == 5

    def test_custom_chunk_size_flag(self, tmp_dir):
        path = write_doc(tmp_dir, make_doc(30))
        result = subprocess.run(
            [sys.executable, SCRIPT_PATH, path, "--chunk-size", "10"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert len(data) == 3

    def test_missing_file_exits_nonzero(self):
        result = subprocess.run(
            [sys.executable, SCRIPT_PATH, "/nonexistent/doc.md"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
