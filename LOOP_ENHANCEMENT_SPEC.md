# Autonomous Loop Engineering & Harness Enhancements Specification
**Target Project:** `brave-experiments/brave-dev-loop`  
**Document Version:** 1.0.0  
**Status:** Architecture Blueprint & Implementation Handoff  
**Scope:** Deep Research, Comparative Analysis, and 3 End-to-End Required Feature Specifications

---

## Table of Contents
1. [Project Deep Dive: Current State & Mechanics](#1-project-deep-dive-current-state--mechanics)
2. [Git History Audit: Recent Merged & Open PRs](#2-git-history-audit-recent-merged--open-prs)
3. [Deep Research: Loop Engineering Literature & Industry State-of-the-Art](#3-deep-research-loop-engineering-literature--industry-state-of-the-art)
4. [Comparative Analysis of Open-Source Harnesses](#4-comparative-analysis-of-open-source-harnesses)
5. [Feature 1 Blueprint: Real-Time Loop Observability & Interactive Control Plane](#5-feature-1-blueprint-real-time-loop-observability--interactive-control-plane)
6. [Feature 2 Blueprint: Multi-Tier Verification Ladder & Intelligent Output Folder](#6-feature-2-blueprint-multi-tier-verification-ladder--intelligent-output-folder)
7. [Feature 3 Blueprint: Cross-Session Episodic Memory & Adaptive Budget Governor](#7-feature-3-blueprint-cross-session-episodic-memory--adaptive-budget-governor)
8. [Cross-Agent Coordination, Integration Matrix & Repository Standards](#8-cross-agent-coordination-integration-matrix--repository-standards)

---

## 1. Project Deep Dive: Current State & Mechanics

### 1.1 What `brave-dev-loop` Does
`brave-dev-loop` is a production-grade autonomous coding loop designed to drive long-horizon software engineering iterations against complex, multi-million-line repositories (principally `brave-core`, Chromium patches, `bravebot`, and `brave-dev-loop` itself).

The loop is fundamentally decoupled: **the loop is the product; the underlying AI agent is swappable**. Supported agents include:
- **Claude Code CLI** (default)
- **Codex**
- **Cursor Agent** (`cursor-agent`)
- **Bravebot** (Brave's native Rust-based harness)

### 1.2 The Core State Machine
The lifecycle of every issue/task is governed by a strict deterministic state machine (`docs/STATE-MACHINE.md` and `docs/workflow-state-machine.md`):

```
       START (Backlog / Issue)
         │
         ▼
    ┌─────────┐
    │ pending │ ───► Implement code in isolated worktree; run acceptance tests
    └─────────┘
         │ (Tests pass & committed)
         ▼
   ┌───────────┐
   │ committed │ ───► Push branch to remote; create GitHub PR (often draft)
   └───────────┘
         │ (PR created)
         ▼
     ┌────────┐
     │ pushed │ ◄──┐ (Reviewer comment triage / CI checks / Re-requests)
     └────────┘ ───┘
         │ (Approved + CI green + Unresolved comments == 0)
         ▼
     ┌────────┐
     │ merged │ ───► Terminal state
     └────────┘
```

Auxiliary terminal states include:
- `skipped`: Definitively blocked by external dependency or explicit human operator decision.
- `invalid`: Malformed story or issue.

### 1.3 Key Architectural Components
1. **Selection Queue & Prioritization Engine (`scripts/select-task.py`):**
   Prioritizes reviewer responsiveness over greenfield coding. Tier hierarchy:
   - `TIER_URGENT (1)`: `pushed` stories where reviewer commented last (`lastActivityBy == "reviewer"`).
   - `TIER_HIGH (2)`: `committed` stories waiting to be pushed.
   - `TIER_PENDING_RESERVED (2.5)`: Optional first-slot reservation for pending work.
   - `TIER_STALE (3)`: `pushed` stories unreviewed for >24 hours (triggers reminders).
   - `TIER_NORMAL (4)`: `pending` greenfield tasks.
   - `TIER_MEDIUM (5)`: `pushed` stories awaiting review (checked recently).
   - `TIER_QUARANTINE (6)`: Retried repeatedly beyond `MAX_PENDING_ATTEMPTS`.

2. **Loop Orchestration (`run.sh`):**
   Drives iterations sequentially or concurrently up to `maxConcurrentRuns`. Manages run slots, process groups, flock/directory locks, quiet watchdogs (`scripts/timeout-tree.sh`), Stop hooks (`scripts/iteration-stopped-short.py`), and log capture.

3. **Autonomous Reviewer Bot (`scripts/review-session.sh` & `/review-prs`):**
   Operates as an independent scheduled persona (`netzenbot-reviewer`) distinct from the developer bot (`netzenbot`). It shards candidate PR diffs across parallel detect subagents, runs validation subagents, and applies structured review verdicts (`APPROVE` or `REQUEST_CHANGES`).

4. **Multi-Worktree Confinement (`scripts/lib/pr_worktree.py`):**
   Isolates development into sibling checkouts (`../<repo>-<issue>`) to preserve clean working trees and prevent git index corruption.

---

## 2. Git History Audit: Recent Merged & Open PRs

To ensure new features align with the project roadmap and introduce **zero conflicts or redundancies**, we conducted a comprehensive review of recent Git history.

### 2.1 Recent Merged PRs Analysis (Past 1–10 Days)
- **PR #176 (`setup-prompts-for-bot-gh-login`):** Enhanced `make setup` to prompt and configure GitHub CLI authentication for both the bot and reviewer accounts, preventing unintended machine-owner attribution.
- **PR #175 (`review-requested-settle-before-counting`):** Addressed GitHub API eventual consistency. Added `REVIEW_REQUESTED_SETTLE_SECONDS` delay before counting an unanswered review attempt to accommodate replication lag.
- **PR #174 & #166 (`bravebot-host-only-gates` / `dedupe-bravebot-gates`):** Optimized gate execution; Docker cross-platform verification gates only run when targeting external platforms.
- **PR #173 (`sccache-for-worktree-builds`):** Documented and configured shared host `sccache` across throwaway worktrees to prevent cold C++ compilation overhead.
- **PR #172 (`session-logs-per-run`):** Refactored log structure so each run stores logs in an isolated session directory (`~/.brave-dev-loop/<session>/`).
- **PR #171 (`confine-review-sessions`):** **Major security milestone.** Strictly sandboxed review sessions: stripped GitHub tokens from Claude sessions, enforced `--permission-mode dontAsk`, isolated work directories, and prohibited arbitrary network or token access to mitigate prompt injection.
- **PR #170 & #169 (`rerequested-review-always-gets-a-verdict` / `request-changes-for-blocking-review`):** Formalized review bot outputs: blocking issues must issue `REQUEST_CHANGES`, passing re-reviews must issue `APPROVE`.
- **PR #168, #165, #164, #163:** Enforced clean separation of Developer Bot (`netzenbot`) and Reviewer Bot (`netzenbot-reviewer`) credentials and PR re-request webhooks.

### 2.2 Unmerged Open Pull Requests Audit
1. **PR #56 (`add-bug-discovery`, authored by shisirbasel):**
   - *Status:* Open (stalled for ~3 months).
   - *Scope:* Implements `.claude/skills/discover-bugs` to run cyclic background scans across code pods and automatically file new bug issues to GitHub.
   - *Conflict Check:* **No conflict.** Our proposed features focus on loop observability, test verification ladders, and cross-session memory during task execution—not automated issue filing.
2. **PR #53 (`browser-mcp-verify`, authored by shisirbasel):**
   - *Status:* Open (stalled for ~4 months).
   - *Scope:* Adds Chrome DevTools MCP connection to a local macOS dev Brave binary (`127.0.0.1:9223`) for visual verification of UI stories.
   - *Conflict Check:* **No conflict.** Our proposed features do not depend on dev-browser MCPs or local macOS desktop instances.

---

## 3. Deep Research: Loop Engineering Literature & Industry State-of-the-Art

The AI software engineering discipline has transitioned from **Prompt Engineering** (2022–2023) to **Harness Engineering** (2024–2025), and is now entering **Loop Engineering** (2025–2026).

### 3.1 Foundational Research Papers
1. **"Stop Hand-Holding Your Coding Agent: Engineering the Loops that Replace Step-by-Step Prompting"**  
   *Author:* Sandeco Macedo (June 2026, [arXiv:2607.00038](https://arxiv.org/abs/2607.00038))  
   *Key Insights:*
   - **The Verification Ladder:** Un-laddered loops (relying only on monolithic "run tests" commands) suffer from high failure rates. High-performing loops employ a phased ladder: *Syntax AST check -> Compiler diagnostics -> Target unit regression -> Integration suites -> State verification*.
   - **Anti-Pattern: Model-as-Judge Fragility:** Agents allowed to self-declare "all tests passed" without machine-verifiable exit assertions frequently hallucinate completion.
   - **Token Budget Governors:** Unbounded retry loops experience exponential cost decay. Autonomous loops require dynamic circuit-breakers triggered by semantic failure clustering.

2. **"Loop Engineering: Building Blocks, Adoption, and Impact"**  
   *Authors:* Jai Lal Lulla, Vahram Nersesyan, Seyedmoein Mohsenimofidi, Christoph Treude, Sebastian Baltes (August 2026, [arXiv:2608.21884](https://arxiv.org/abs/2608.21884))  
   *Key Insights:*
   - Examines empirical patterns across 36,000+ autonomous agent repositories.
   - Core failure modes identified: **Context Flooding** (build outputs consuming 80%+ of window tokens), **Session Amnesia** (repeating identical failed diffs across loop iterations), and **Telemetry Black Holes** (operator inability to observe or steer running loops).

3. **"LoopsBench: From Harness Engineering to Loop Engineering in Coding Agent Evaluation"**  
   *Authors:* Han Li et al. (August 2026, [arXiv:2608.00267](https://arxiv.org/abs/2608.00267))  
   *Key Insights:*
   - Establishes that single-iteration benchmarks (HumanEval, standard SWE-bench) fail to evaluate real-world loops. Loop resilience requires sustaining correct state over multi-iteration dependency DAGs with cross-iteration memory.

4. **"LoopArena: Benchmarking Models as Runtime Controllers for Loop Engineering"**  
   *Authors:* Research Group (August 2026, [arXiv:2608.28281](https://arxiv.org/abs/2608.28281))  
   *Key Insights:*
   - Decouples the **Runtime Controller** (monitors budgets, inspects diff sizes, folds logs, arbitrates transitions) from the **Worker Agent** (writes code). Demonstrates a 34% reduction in iteration regressions when Controller oversees Worker.

---

## 4. Comparative Analysis of Open-Source Harnesses

| Framework | Core Strength | Key Architectural Feature | Code/Pattern to Borrow |
| :--- | :--- | :--- | :--- |
| **OpenHands** (`All-Hands-AI/OpenHands`) | Event Sourcing & Web Control UI | Unified `EventStream` architecture with SSE/WebSocket live streaming | `openhands/events/stream.py` (Action/Observation event model) |
| **Aider** (`paul-gauthier/aider`) | Real-Time Diagnostics & Fast In-Loop Linter | In-process AST linter & Rich streaming console | `aider/linter.py` (universal sub-second linter) & `aider/io.py` |
| **SWE-agent** (`princeton-nlp/SWE-agent`) | Agent-Computer Interface (ACI) | Shell output folding & line-indexed error parsing | `sweagent/environment/hooks.py` (error folding regex engine) |
| **Textual** (`textualize/textual`) | Terminal UI Application Framework | Asynchronous Python TUI with reactive state management | Multi-pane log viewer, progress bars, and hotkey listeners |

---

## 5. Feature 1 Blueprint: Real-Time Loop Observability & Interactive Control Plane

### 5.1 Motivation & Problem Statement
Currently, `run.sh` operates as a black box. In headless mode, outputs stream to `iteration-loop-1.log` or unstructured JSONL logs under `~/.brave-dev-loop/<session>/`. The developer must run `tail -f data/progress.txt` or periodically run `run.sh --status`.  
There is no unified live dashboard to observe:
1. Real-time phase (Task Selection -> Worktree Checkout -> Exploration -> File Edits -> Compilation -> Test Execution -> Gate Verification).
2. Live git diffs as they are produced in the worktree.
3. Cumulative token/cost velocity per story and session.
4. Active worktrees and PR review queues.
5. **Human-in-the-Loop (HITL) Steering:** There is currently no way to pause, inject a mid-run hint, or steer an agent stuck in a minor syntax issue without killing the entire process with `Ctrl+C` and running `reset-run.sh --stale`.

### 5.2 Hand-Holding Guide for Agent 1 (Zero Prior Context)

#### Step 1: External Documentation & Codebases to Study
1. **OpenHands EventStream Architecture:**  
   - Inspect: `https://github.com/All-Hands-AI/OpenHands/blob/main/openhands/events/stream.py`  
   - Inspect: `https://github.com/All-Hands-AI/OpenHands/blob/main/openhands/server/`  
   - *Key takeaway:* Notice how all agent actions (`CmdRunAction`, `FileEditAction`) and harness observations (`CmdOutputObservation`) inherit from a typed base `Event` class and are appended to a monotonic, file-backed stream that WebSockets broadcast.
2. **Textual / Rich Terminal Architecture:**  
   - Inspect: `https://github.com/Textualize/textual` and `https://github.com/Textualize/rich`  
   - Docs: `https://textual.textualize.io/`
3. **Paper:** Macedo 2026 (arXiv:2607.00038) Section 3: "Loop Observability and Runtime Event Bus".

#### Step 2: Target Files in `brave-dev-loop`
- **Inspect:** `run.sh` (lines 750–930, where agents are launched via `exec-clean.sh` and tee'd into `$ITERATION_LOG`).
- **Inspect:** `scripts/run-status.sh` (how run slots and PIDs are inspected).
- **Inspect:** `scripts/lib/slots.py` and `scripts/lib/run-slots.sh` (slot claims and process locks).
- **Modify:** `run.sh` (hook output pipes into the event stream emitter).
- **Create:** `scripts/lib/event_stream.py` (core event sourcing and file-backed SSE emitter).
- **Create:** `scripts/web_dashboard.py` (FastAPI / Starlette local web dashboard on port `8501`).
- **Create:** `scripts/tui_dashboard.py` (Rich/Textual terminal dashboard invoked with `./run.sh 10 dashboard`).
- **Create:** `scripts/steer-loop.py` (CLI tool to inject hints or pause/resume running slots).

#### Step 3: End-to-End Architectural Design

```
┌────────────────────────────────────────────────────────────────────────┐
│                        AGENT RUNNER (run.sh)                           │
│  Claude / Codex / Cursor / Bravebot  stdout/stderr (JSON / stream-json)│
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│               EVENT STREAM EMITTER (scripts/lib/event_stream.py)       │
│  - Parses raw agent events (tool_use, file_edit, bash_cmd, token_cost)  │
│  - Appends to ~/.brave-dev-loop/<session>/events.jsonl                 │
│  - Manages IPC control socket: ~/.brave-dev-loop/<session>/control.sock │
└───────────────────┬────────────────────────────────┬───────────────────┘
                    │                                │
                    ▼                                ▼
┌─────────────────────────────────────┐  ┌───────────────────────────────┐
│       LOCAL WEB DASHBOARD           │  │      RICH TERMINAL UI (TUI)   │
│     (scripts/web_dashboard.py)      │  │    (scripts/tui_dashboard.py) │
│ - FastAPI + WebSockets / SSE        │  │ - 3-Pane Split View           │
│ - Live Diff Viewer (Monaco/Prism)   │  │   Left: Stories & State       │
│ - Token / Cost Ticker & Progress    │  │   Center: Live Agent Stream   │
│ - Interactive Steer / Pause Console │  │   Right: Tests, Diff, Budget  │
└─────────────────────────────────────┘  └───────────────────────────────┘
```

#### Step 4: Code to Borrow and Adapt
From `OpenHands` (`openhands/events/event.py`), adapt the lightweight Python event model:
```python
# Borrowed & adapted from OpenHands event streaming pattern
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json


@dataclass
class LoopEvent:
    timestamp: str
    slot: int
    story_id: str
    event_type: (
        str  # "PHASE_CHANGE", "TOOL_CALL", "FILE_DIFF", "TOKEN_USAGE", "GATE_STATUS"
    )
    payload: dict

    @classmethod
    def create(cls, slot: int, story_id: str, event_type: str, payload: dict):
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            slot=slot,
            story_id=story_id,
            event_type=event_type,
            payload=payload,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self))
```

#### Step 5: Step-by-Step Implementation Roadmap
1. **Phase 1: Event Bus Foundation:**  
   Implement `scripts/lib/event_stream.py`. Ensure atomic line appends to `~/.brave-dev-loop/<session>/events.jsonl` using existing `lib/file_lock.py` conventions.
2. **Phase 2: Runner Instrumentation:**  
   In `run.sh`, wrap the stream consumer (where `tee -a "$ITERATION_LOG"` runs) with a stream parser pipe that ingests Claude's `stream-json` / Codex's `--json` and emits normalized `LoopEvent` records.
3. **Phase 3: Interactive Steering IPC:**  
   Implement a FIFO or UNIX socket listener (`~/.brave-dev-loop/<session>/control.fifo`). When an operator enters a hint via web UI or CLI (`./scripts/steer-loop.py --slot 1 "Do not touch file X, use existing API Y"`), the runner writes the hint into the next turn's prompt or injects it into the running session without dropping the worktree.
4. **Phase 4: Web & TUI Interfaces:**  
   - Build `scripts/web_dashboard.py` using standard Python library / FastAPI. Serve a modern dark-mode frontend showing: (a) Kanban board of PRD stories; (b) Real-time agent thought stream; (c) Live git diff viewer; (d) Control actions (Pause, Resume, Steer, Abort).
   - Build `scripts/tui_dashboard.py` using `rich.live` or `textual` for pure terminal workflows.

#### Step 6: Testing & Verification Plan
- **Unit Tests:** Create `tests/test_event_stream.py` verifying:
  - Event serialization/deserialization.
  - Concurrency safety across multiple run slots.
  - FIFO IPC control signal handling.
- **Integration Test:** Run a mock 1-iteration run (`./run.sh 1 --agent claude`) while verifying that `events.jsonl` receives structured events and the dashboard reflects real-time phase updates.
- **Linter & Formatting:** Run `make lint` (must pass `ruff check` and `ruff format --check`).

---

## 6. Feature 2 Blueprint: Multi-Tier Verification Ladder & Intelligent Output Folder

### 6.1 Motivation & Problem Statement
In large C++ / Chromium codebases (`brave-core`), running `ninja -C out/Default chrome` or running comprehensive test suites outputs tens of thousands of lines of compiler output, include warnings, and linker notes.  
Currently:
1. When a C++ test or compilation step fails, the raw terminal stream floods the context window.
2. Under `timeout-tree.sh` and Claude's context limits, key compiler errors get truncated, or the agent is overwhelmed by ninja log spam.
3. The bot frequently jumps directly from editing code to running the full multi-hour acceptance test suite, violating the **Verification Ladder** principle (Macedo, 2026), which dictates that fast AST linting and targeted regression tests must execute before heavy builds.

### 6.2 Hand-Holding Guide for Agent 2 (Zero Prior Context)

#### Step 1: External Documentation & Codebases to Study
1. **SWE-agent Shell Output Folding & Parsing:**  
   - Inspect: `https://github.com/princeton-nlp/SWE-agent/blob/main/sweagent/environment/hooks.py`  
   - Inspect: `https://github.com/princeton-nlp/SWE-agent/blob/main/sweagent/tools/parsing.py`  
   - *Key takeaway:* Look at how SWE-agent uses regex problem matchers to fold 10,000 lines of pytest/compiler logs down to just the failing assertion blocks, stripping unchanged success messages.
2. **Aider Fast In-Process Linter:**  
   - Inspect: `https://github.com/paul-gauthier/aider/blob/main/aider/linter.py`  
   - *Key takeaway:* Aider runs sub-second AST and syntax linting directly on modified files before invoking the full build or test suite.
3. **Paper:** Macedo 2026 (arXiv:2607.00038) Section 4: "The Five Tiers of Verification Ladders in Long-Horizon Engineering".

#### Step 2: Target Files in `brave-dev-loop`
- **Inspect:** `scripts/wait-gate.sh` (how gates and test runners are polled and awaited).
- **Inspect:** `docs/workflow-pending.md` (Step 6 "Run tests" and Step 9 "Verify on rebased tree").
- **Inspect:** `projects/brave-core/docs/testing.md` (project-specific C++ test commands, `RunUntilIdle`, and filter flags).
- **Create:** `scripts/lib/output_folder.py` (regex-driven stream folder for ninja, clang++, rustc, and gtest).
- **Create:** `scripts/lib/verification_ladder.py` (ladder coordinator executing Tier 0 -> Tier 1 -> Tier 2 -> Tier 3).
- **Modify:** `scripts/wait-gate.sh` (integrate output folding so gate log files contain both raw logs and clean `.diagnostic.json` extracts).

#### Step 3: End-to-End Architectural Design

```
                     CODE CHANGE COMMITTED IN WORKTREE
                                     │
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ TIER 0: SUB-SECOND SYNTAX & AST LINTER (scripts/lib/fast_linter.py)    │
 │ - Runs ruff (Python), clang-format / clang-tidy dry-run (C++), etc.    │
 │ - Duration: < 2 seconds. Exits early if basic syntax is broken.         │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │ PASS
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ TIER 1: INCREMENTAL TARGET COMPILER & ERROR FOLDER                     │
 │ - Invokes ninja/cargo only for modified build target                   │
 │ - Output Folder captures 50,000-line build stream                     │
 │ - Strips: Entering directory, [1/45000] progress, clean object files   │
 │ - Extracts: File path, Line number, Clang error diagnostics, Stack    │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │ PASS
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ TIER 2: TARGETED REGRESSION SUITE (Targeted Unit Tests)                │
 │ - Automatically maps changed files to specific gtest/pytest filter     │
 │   e.g. components/brave_shields/* -> brave_unit_tests --gtest_filter  │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │ PASS
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ TIER 3: FULL ACCEPTANCE CRITERIA GATE (scripts/wait-gate.sh)           │
 │ - Final rebased tree verification before advancing to 'committed'      │
 └────────────────────────────────────────────────────────────────────────┘
```

#### Step 4: Code to Borrow and Adapt
From `SWE-agent` (`sweagent/environment/hooks.py`), adapt the compiler/test error extraction regex engine:
```python
# Borrowed & adapted from SWE-agent output parsing pattern
import re

CLANG_ERROR_PATTERN = re.compile(
    r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<col>\d+):\s+error:\s+(?P<msg>.+?)(?=\n[^:\n]+:\d+:\d+:|\n\[\d+/\d+\]|\Z)",
    re.MULTILINE | re.DOTALL,
)

GTEST_FAILURE_PATTERN = re.compile(
    r"\[\s+FAILED\s+\]\s+(?P<test>[\w\.\/]+).+?\n(?P<failure_details>.*?)(?=\[\s+(?:RUN|OK|FAILED)\s+\]|\Z)",
    re.MULTILINE | re.DOTALL,
)


def fold_build_output(raw_output: str, max_diagnostics: int = 5) -> dict:
    """Folds multi-megabyte C++/ninja build streams into actionable diagnostics."""
    errors = []
    for match in CLANG_ERROR_PATTERN.finditer(raw_output):
        errors.append(
            {
                "type": "compiler_error",
                "file": match.group("file").strip(),
                "line": int(match.group("line")),
                "column": int(match.group("col")),
                "message": match.group("msg").strip()[:1000],  # bounded length
            }
        )
        if len(errors) >= max_diagnostics:
            break

    failures = []
    for match in GTEST_FAILURE_PATTERN.finditer(raw_output):
        failures.append(
            {
                "type": "test_failure",
                "test": match.group("test").strip(),
                "details": match.group("failure_details").strip()[:2000],
            }
        )
        if len(failures) >= max_diagnostics:
            break

    return {
        "total_raw_lines": raw_output.count("\n"),
        "compiler_errors": errors,
        "test_failures": failures,
        "summary": (
            f"Found {len(errors)} compiler errors, {len(failures)} test failures"
            f" in {raw_output.count(chr(10))} lines of build output."
        ),
    }
```

#### Step 5: Step-by-Step Implementation Roadmap
1. **Phase 1: Output Folding Module:**  
   Implement `scripts/lib/output_folder.py`. Support both C++/Clang/Ninja streams and Rust/cargo streams. Output structured JSON alongside raw logs.
2. **Phase 2: Prompt Diagnostic Envelope:**  
   When a build or gate fails, instead of feeding raw terminal output back to Claude/Codex, format the diagnostic as:
   ```xml
   <DIAGNOSTIC_FAILURE tier="compiler">
     <FILE>brave/components/brave_shields/browser/shields_service.cc</FILE>
     <LINE>142</LINE>
     <ERROR>no matching member function for call to 'SetAdBlockEnabled'</ERROR>
     <EXCERPT>
       shields_service.cc:142:12: error: no member named 'SetAdBlockEnabled' in 'brave_shields::ShieldsService'
     </EXCERPT>
   </DIAGNOSTIC_FAILURE>
   ```
3. **Phase 3: Verification Ladder Runner:**  
   Create `scripts/run-verification-ladder.sh`. It accepts the target repo worktree and modified branch, runs Tier 0, then Tier 1, then Tier 2. If any tier fails, it immediately terminates and generates the `<DIAGNOSTIC_FAILURE>` payload for the agent.
4. **Phase 4: Workflow Integration:**  
   Update `docs/workflow-pending.md` and `scripts/wait-gate.sh` to instruct the bot to invoke the verification ladder rather than executing unstructured raw commands.

#### Step 6: Testing & Verification Plan
- **Unit Tests:** Create `tests/test_output_folder.py` with mock 5,000-line C++ Ninja dumps, Rust compile logs, and GTest runs. Assert that the folder accurately extracts error locations and caps output size under 4KB.
- **Ladder Gate Tests:** Create `tests/test_verification_ladder.py` testing early-exit behavior on Tier 0 lint failures.
- **Linter & Formatting:** Run `make lint` and `make test`.

---

## 7. Feature 3 Blueprint: Cross-Session Episodic Memory & Adaptive Budget Governor

### 7.1 Motivation & Problem Statement
Currently, when a story fails during an iteration (e.g. compilation error or test timeout) or receives review feedback (`REQUEST_CHANGES`), `iteration-stopped-short.py` logs a short line to `progress.txt` and the iteration terminates.  
In the next iteration:
1. The agent starts with a **clean session**. It has zero memory of previous failed attempts, rejected hypotheses, or dead-end architectural approaches.
2. As a consequence, agents frequently fall into **"rabbit holes"**—repeating the exact same failed code edits or alternating between two broken implementations.
3. Furthermore, there is **no token/budget governor**. A story can cycle through 10 iterations, burning $100+ in API tokens, without any circuit-breaker mechanism detecting that the agent is making zero forward progress.

### 7.2 Hand-Holding Guide for Agent 3 (Zero Prior Context)

#### Step 1: External Documentation & Codebases to Study
1. **OpenHands Memory & State Reflection:**  
   - Inspect: `https://github.com/All-Hands-AI/OpenHands/tree/main/openhands/memory`  
   - *Key takeaway:* Notice how OpenHands maintains an episodic memory store that summarizes completed attempts and records negative constraints ("What didn't work").
2. **Anthropic Harness Guide on Evaluator-Optimizer Loops:**  
   - Review: *Anthropic: Building Effective Agents* (Section: "Reflection and Memory Patterns in Multi-Turn Systems").
3. **Papers:**  
   - Macedo 2026 (arXiv:2607.00038) Section 5: "Budget Governors and Circuit Breakers in Long-Running Loops".  
   - Li et al. LoopsBench (arXiv:2608.00267) Section 3.2: "Cross-Iteration State Continuity".

#### Step 2: Target Files in `brave-dev-loop`
- **Inspect:** `scripts/select-task.py` (where stories are selected and candidate data is prepared).
- **Inspect:** `run.sh` (lines 750–820, where `AGENT_PROMPT` is constructed).
- **Inspect:** `scripts/iteration-stopped-short.py` (called on session termination to assess what happened).
- **Inspect:** `config.example.json` (configuration schema for cost thresholds and budget caps).
- **Create:** `scripts/lib/story_memory.py` (persistent episodic memory manager storing structured logs under `data/memory/<story-id>.json`).
- **Create:** `scripts/lib/budget_governor.py` (tracks cumulative token expenditure and detects semantic failure loops).
- **Create:** `scripts/summarize-iteration-memory.py` (runs a micro-reflection prompt after a failed iteration to extract learned constraints).

#### Step 3: End-to-End Architectural Design

```
             ITERATION N ENDS (Failed gate / Unresolved review / Stopped short)
                                     │
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ POST-ITERATION REFLECTION (scripts/summarize-iteration-memory.py)      │
 │ - Analyzes git diff, compiler error, and failure diagnostic            │
 │ - Extracts:                                                            │
 │   * Hypothesis tested: "Modified mojom interface for shields"          │
 │   * Why it failed: "Caused ABI mismatch in upstream chromium base"     │
 │   * Discovered constraint: "Must use createInterfaceApi wrapper"       │
 │ - Updates data/memory/<story-id>.json                                  │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ BUDGET GOVERNOR & CIRCUIT BREAKER (scripts/lib/budget_governor.py)     │
 │ - Tallies cumulative tokens & USD cost for this story across all runs  │
 │ - Checks semantic oscillation: Did Iteration N repeat Iteration N-2?   │
 │ - If cost > maxCostPerStory ($15) OR consecutive identical failures:  │
 │   -> Trips circuit breaker: Marks story 'skipped' (stuck_rabbit_hole)  │
 │   -> Emits Signal notification via scripts/signal-notify.sh            │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼ (If under budget)
 ┌────────────────────────────────────────────────────────────────────────┐
 │ ITERATION N+1 START (run.sh & scripts/select-task.py)                  │
 │ - Injects Episodic Memory into AGENT_PROMPT:                          │
 │   "## Prior Failed Attempts & Discovered Constraints:                  │
 │    - Attempt 1: Modifying mojom failed due to ABI mismatch.            │
 │      DO NOT try this again.                                            │
 │    - Current cumulative story expenditure: $4.20 / $15.00 limit."      │
 └────────────────────────────────────────────────────────────────────────┘
```

#### Step 4: Code to Borrow and Adapt
From `OpenHands` and standard circuit-breaker patterns, adapt the episodic memory schema:
```python
# Borrowed & adapted from OpenHands episodic memory and budget governor pattern
from dataclasses import asdict, dataclass, field
import json
import os
from typing import List, Optional


@dataclass
class AttemptRecord:
    iteration_num: int
    timestamp: str
    status_at_start: str
    hypothesis: str
    failure_reason: str
    negative_constraints: List[str]  # "Do not do X"
    cost_usd: float
    diff_hash: str


@dataclass
class StoryMemory:
    story_id: str
    cumulative_cost_usd: float = 0.0
    total_iterations: int = 0
    attempts: List[AttemptRecord] = field(default_factory=list)
    active_constraints: List[str] = field(default_factory=list)

    def add_attempt(self, record: AttemptRecord):
        self.attempts.append(record)
        self.total_iterations += 1
        self.cumulative_cost_usd += record.cost_usd
        for c in record.negative_constraints:
            if c not in self.active_constraints:
                self.active_constraints.append(c)

    def format_for_prompt(self) -> str:
        if not self.attempts:
            return ""
        lines = ["## Prior Attempts & Discovered Constraints (DO NOT REPEAT):"]
        for att in self.attempts[-3:]:  # last 3 attempts
            lines.append(
                f"- Attempt {att.iteration_num}: Tried '{att.hypothesis}' -> Failed:"
                f" {att.failure_reason}"
            )
        if self.active_constraints:
            lines.append("\nDiscovered architectural rules for this story:")
            for c in self.active_constraints:
                lines.append(f"  * {c}")
        return "\n".join(lines)
```

#### Step 5: Step-by-Step Implementation Roadmap
1. **Phase 1: Memory & Budget Store Foundation:**  
   Implement `scripts/lib/story_memory.py` and `scripts/lib/budget_governor.py`. Store memory in `data/memory/<story-id>.json`. Use atomic file write patterns.
2. **Phase 2: Post-Iteration Reflection Hook:**  
   In `run.sh` (post-session handling, near line 926), if the iteration ended without advancing the story to `committed` or `merged`, call `scripts/summarize-iteration-memory.py`. This generates an `AttemptRecord` using Claude/Codex micro-reflection (or regex analysis of git diff and compiler failure).
3. **Phase 3: Prompt Injection in Task Selection:**  
   In `run.sh` (around line 770), query `story_memory.format_for_prompt()` and append it to `AGENT_PROMPT`.
4. **Phase 4: Circuit-Breaker Integration:**  
   In `scripts/select-task.py`, before selecting a story, check `budget_governor.is_circuit_broken(story_id)`. If tripped, quarantine or skip the story, write the root cause to `progress.txt`, and notify the team via `scripts/signal-notify.sh`.

#### Step 6: Testing & Verification Plan
- **Unit Tests:** Create `tests/test_story_memory.py` and `tests/test_budget_governor.py`:
  - Test memory prompt formatting.
  - Test oscillation detection (repeated diff hashes).
  - Test budget limit enforcement ($15 cap tripping circuit breaker).
- **Integration Test:** Simulate a 2-iteration story where Iteration 1 fails, verify that Iteration 2 prompt receives `<Prior Attempts>` context.
- **Linter & Formatting:** Run `make lint` and `make test`.

---

## 8. Cross-Agent Coordination, Integration Matrix & Repository Standards

To ensure that 3 independent agents can implement Features 1, 2, and 3 concurrently without collisions:

### 8.1 File Ownership Matrix

| Feature | Primary Agent | Exclusive Files to Create | Files Modified (Coordinated Edits) |
| :--- | :--- | :--- | :--- |
| **Feature 1: Observability & Control UI** | Agent 1 | `scripts/lib/event_stream.py`<br>`scripts/web_dashboard.py`<br>`scripts/tui_dashboard.py`<br>`scripts/steer_loop.py` | `run.sh` (stream-json tap point)<br>`config.example.json` (`dashboard` block) |
| **Feature 2: Verification Ladder & Output Folder** | Agent 2 | `scripts/lib/output_folder.py`<br>`scripts/lib/verification_ladder.py`<br>`scripts/run-verification-ladder.sh` | `scripts/wait-gate.sh` (folding hooks)<br>`docs/workflow-pending.md` |
| **Feature 3: Episodic Memory & Budget Governor** | Agent 3 | `scripts/lib/story_memory.py`<br>`scripts/lib/budget_governor.py`<br>`scripts/summarize_iteration_memory.py` | `scripts/select-task.py` (memory prompt injection)<br>`run.sh` (reflection hook) |

### 8.2 Shared Coding Standards & Guardrails
1. **Python Compatibility:** Python >= 3.9 (PEP 668 compliant; use `.venv` or system python via `Makefile`).
2. **Tooling & Linting:** Every change must pass `make lint` (`ruff check .` and `ruff format --check .`). Long lines are tolerated (`E501`), but clean formatting is mandatory.
3. **Security Standards (Crucial - Follow PR #171):**
   - No secrets, API keys, or GitHub write tokens may ever be injected into the agent prompt or written to memory JSON files.
   - Any script reading untrusted PR diffs or issues must execute under the security boundaries established in PR #171.
4. **No AI Attribution:** Strict repo rule: Never add "Co-Authored-By", "Generated with Claude Code", or AI attribution to commit messages or PR descriptions.
5. **Atomic File Locking:** All concurrent data file updates (`data/prd.json`, `data/memory/*.json`) must use `lib/file_lock.py` or `lib/prd_store.py` to prevent race conditions during multi-slot runs.

---
*End of Blueprint. Next agents can directly pick up Section 5 (Agent 1), Section 6 (Agent 2), or Section 7 (Agent 3) and follow the step-by-step instructions.*
