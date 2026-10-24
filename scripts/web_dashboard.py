#!/usr/bin/env python3
"""Web Dashboard & Control Plane for brave-dev-loop.

Provides a real-time, zero-dependency browser control console for:
- Live streaming agent thoughts, tool calls, and phase transitions.
- Story backlog & PRD lifecycle visualization (pending -> committed -> pushed -> merged).
- Live Git diff viewer of active worktree changes.
- Interactive Human-in-the-Loop (HITL) steering console (pause, resume, inject hints).

Usage:
    ./scripts/web_dashboard.py [--port 8501] [--host 127.0.0.1] [--open]
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BOT_DIR, "scripts"))

from lib.event_stream import (
    find_all_sessions,
    get_sessions_dir,
    is_session_paused,
    read_events,
    set_paused_state,
    submit_steer_hint,
)

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>brave-dev-loop Control Plane</title>
  <style>
    :root {
      --bg: #090d16;
      --card-bg: #121927;
      --card-border: #1e293b;
      --accent: #38bdf8;
      --accent-glow: rgba(56, 189, 248, 0.2);
      --success: #34d399;
      --warning: #fbbf24;
      --danger: #f87171;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --mono: 'JetBrains Mono', 'Fira Code', Menlo, monospace;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    header {
      background: #0f172a;
      border-bottom: 1px solid var(--card-border);
      padding: 12px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .header-title {
      display: flex;
      align-items: center;
      gap: 12px;
      font-size: 1.15rem;
      font-weight: 700;
      letter-spacing: -0.5px;
    }
    .badge-live {
      background: rgba(52, 211, 153, 0.15);
      color: var(--success);
      border: 1px solid rgba(52, 211, 153, 0.4);
      padding: 3px 8px;
      border-radius: 9999px;
      font-size: 0.72rem;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 5px;
    }
    .pulse-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--success);
      box-shadow: 0 0 8px var(--success);
      animation: pulse 2s infinite;
    }
    @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.3; } 100% { opacity: 1; } }
    .header-actions {
      display: flex;
      gap: 10px;
      align-items: center;
    }
    button {
      background: #1e293b;
      color: var(--text);
      border: 1px solid #334155;
      padding: 6px 14px;
      border-radius: 6px;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    button:hover { background: #334155; }
    button.primary { background: #0284c7; border-color: #38bdf8; color: white; }
    button.primary:hover { background: #0369a1; }
    button.danger { background: #991b1b; border-color: #f87171; }
    button.danger:hover { background: #7f1d1d; }

    .stats-bar {
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 12px;
      padding: 12px 24px;
      background: #090e1a;
      border-bottom: 1px solid var(--card-border);
    }
    .stat-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 10px 14px;
    }
    .stat-label {
      font-size: 0.7rem;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 4px;
    }
    .stat-value {
      font-size: 1.1rem;
      font-weight: 700;
      font-family: var(--mono);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    main {
      flex: 1;
      display: grid;
      grid-template-columns: 280px 1fr 380px;
      gap: 12px;
      padding: 12px 24px 24px 24px;
      overflow: hidden;
    }
    .pane {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .pane-header {
      padding: 10px 14px;
      background: rgba(255, 255, 255, 0.02);
      border-bottom: 1px solid var(--card-border);
      font-weight: 600;
      font-size: 0.85rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .pane-content {
      flex: 1;
      overflow-y: auto;
      padding: 12px;
    }

    /* Events list */
    .event-card {
      background: #0d1320;
      border: 1px solid #1e293b;
      border-radius: 6px;
      padding: 8px 10px;
      margin-bottom: 8px;
      font-size: 0.82rem;
      transition: border-color 0.15s;
    }
    .event-card:hover { border-color: #38bdf8; }
    .event-meta {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 4px;
    }
    .event-type-badge {
      font-size: 0.68rem;
      font-family: var(--mono);
      padding: 2px 6px;
      border-radius: 4px;
      font-weight: 600;
    }
    .badge-TOOL_CALL { background: rgba(56, 189, 248, 0.15); color: #38bdf8; }
    .badge-PHASE_CHANGE { background: rgba(168, 85, 247, 0.15); color: #c084fc; }
    .badge-GATE_STATUS { background: rgba(52, 211, 153, 0.15); color: #34d399; }
    .badge-FILE_DIFF { background: rgba(251, 191, 36, 0.15); color: #fbbf24; }
    .badge-STEER_HINT { background: rgba(244, 63, 94, 0.15); color: #fb7185; }
    .event-time { font-size: 0.7rem; color: var(--text-muted); font-family: var(--mono); }
    .event-body {
      font-family: var(--mono);
      font-size: 0.78rem;
      color: #cbd5e1;
      white-space: pre-wrap;
      word-break: break-all;
    }

    /* Backlog list */
    .story-item {
      padding: 8px;
      border-radius: 6px;
      border: 1px solid #1e293b;
      background: #0b101b;
      margin-bottom: 6px;
      cursor: pointer;
    }
    .story-item:hover { border-color: #38bdf8; }
    .story-id { font-family: var(--mono); font-size: 0.75rem; color: var(--accent); font-weight: 700; }
    .story-title { font-size: 0.8rem; margin: 3px 0; color: #e2e8f0; }
    .story-status-tag {
      font-size: 0.65rem;
      padding: 1px 5px;
      border-radius: 3px;
      background: #1e293b;
      color: var(--text-muted);
      text-transform: uppercase;
      font-weight: 600;
    }

    /* Diff View */
    .diff-container {
      font-family: var(--mono);
      font-size: 0.75rem;
      white-space: pre;
      color: #94a3b8;
    }
    .diff-add { color: #34d399; background: rgba(52, 211, 153, 0.08); display: block; }
    .diff-del { color: #f87171; background: rgba(248, 113, 113, 0.08); display: block; }
    .diff-header { color: #38bdf8; font-weight: 700; display: block; }

    /* Steer Console */
    .steer-box {
      border-top: 1px solid var(--card-border);
      padding: 12px;
      background: #0b111e;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    textarea {
      background: #060a12;
      border: 1px solid #1e293b;
      border-radius: 6px;
      color: var(--text);
      font-family: var(--mono);
      font-size: 0.8rem;
      padding: 8px;
      resize: none;
      height: 70px;
    }
    textarea:focus { outline: none; border-color: var(--accent); }
  </style>
</head>
<body>
  <header>
    <div class="header-title">
      <span>⚡ brave-dev-loop</span>
      <span class="badge-live"><span class="pulse-dot"></span> LIVE TELEMETRY</span>
    </div>
    <div class="header-actions">
      <button id="btnPause" onclick="togglePause()">⏸ Pause Loop</button>
      <button onclick="fetchData()">🔄 Refresh</button>
    </div>
  </header>

  <section class="stats-bar">
    <div class="stat-card">
      <div class="stat-label">Active Story</div>
      <div class="stat-value" id="statStory">-</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Status</div>
      <div class="stat-value" id="statStatus">-</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Slot & PID</div>
      <div class="stat-value" id="statSlot">-</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Total Events</div>
      <div class="stat-value" id="statEvents">0</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Loop State</div>
      <div class="stat-value" id="statLoopState">READY</div>
    </div>
  </section>

  <main>
    <!-- Left: PRD Story Backlog -->
    <div class="pane">
      <div class="pane-header">
        <span>Story Backlog</span>
        <span id="backlogCount" style="color: var(--text-muted); font-size: 0.75rem;">0 stories</span>
      </div>
      <div class="pane-content" id="backlogList">
        <div style="color: var(--text-muted); font-size: 0.8rem;">Loading stories...</div>
      </div>
    </div>

    <!-- Center: Live Event Stream -->
    <div class="pane">
      <div class="pane-header">
        <span>Live Thought & Event Stream</span>
        <label style="font-size: 0.75rem; color: var(--text-muted); cursor: pointer;">
          <input type="checkbox" id="chkAutoScroll" checked> Auto-scroll
        </label>
      </div>
      <div class="pane-content" id="eventStream">
        <div style="color: var(--text-muted); font-size: 0.8rem;">Listening for loop events...</div>
      </div>
    </div>

    <!-- Right: Worktree Diff & Steer Console -->
    <div class="pane">
      <div class="pane-header">
        <span>Worktree Git Diff</span>
        <button onclick="fetchDiff()" style="padding: 2px 8px; font-size: 0.7rem;">Reload Diff</button>
      </div>
      <div class="pane-content diff-container" id="diffContent">
        (No uncommitted diffs in current worktree)
      </div>
      <div class="steer-box">
        <div style="font-size: 0.75rem; font-weight: 600; color: var(--accent);">Human-in-the-Loop Steering Console</div>
        <textarea id="txtSteer" placeholder="Inject instruction into next agent turn (e.g. 'Focus on line 140, do not modify headers')"></textarea>
        <button class="primary" onclick="sendSteer()">⚡ Inject Hint to Agent</button>
      </div>
    </div>
  </main>

  <script>
    let isPaused = false;
    let currentSession = "";

    async function fetchData() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();

        currentSession = data.session_id || "";
        document.getElementById('statStory').textContent = data.current_story || 'None';
        document.getElementById('statStatus').textContent = data.story_status || 'IDLE';
        document.getElementById('statSlot').textContent = (data.slot ? 'Slot ' + data.slot : 'Idle') + (data.pid ? ' (' + data.pid + ')' : '');
        document.getElementById('statEvents').textContent = data.event_count || '0';

        isPaused = data.is_paused || false;
        document.getElementById('statLoopState').textContent = isPaused ? 'PAUSED' : 'RUNNING';
        document.getElementById('statLoopState').style.color = isPaused ? 'var(--warning)' : 'var(--success)';
        document.getElementById('btnPause').textContent = isPaused ? '▶ Resume Loop' : '⏸ Pause Loop';

        loadBacklog();
        loadEvents();
      } catch (err) {
        console.error("Status fetch error:", err);
      }
    }

    async function loadBacklog() {
      try {
        const res = await fetch('/api/prd');
        const data = await res.json();
        const stories = data.stories || [];
        document.getElementById('backlogCount').textContent = stories.length + ' stories';

        const container = document.getElementById('backlogList');
        if (stories.length === 0) {
          container.innerHTML = '<div style="color: var(--text-muted); font-size: 0.8rem;">No stories in PRD</div>';
          return;
        }

        container.innerHTML = stories.map(s => `
          <div class="story-item">
            <div style="display: flex; justify-content: space-between;">
              <span class="story-id">${s.id || ''}</span>
              <span class="story-status-tag">${s.status || 'pending'}</span>
            </div>
            <div class="story-title">${escapeHtml(s.title || '')}</div>
          </div>
        `).join('');
      } catch (err) {
        console.error("Backlog load error:", err);
      }
    }

    async function loadEvents() {
      try {
        const res = await fetch('/api/events');
        const events = await res.json();
        const container = document.getElementById('eventStream');

        if (!events || events.length === 0) {
          container.innerHTML = '<div style="color: var(--text-muted); font-size: 0.8rem;">No events recorded yet.</div>';
          return;
        }

        container.innerHTML = events.map(ev => {
          let payloadStr = '';
          if (ev.payload) {
            if (typeof ev.payload === 'string') payloadStr = ev.payload;
            else if (ev.payload.tool) payloadStr = 'Tool: ' + ev.payload.tool + '\\nInput: ' + JSON.stringify(ev.payload.input, null, 2);
            else if (ev.payload.hint) payloadStr = 'Hint: ' + ev.payload.hint;
            else payloadStr = JSON.stringify(ev.payload, null, 2);
          }
          return `
            <div class="event-card">
              <div class="event-meta">
                <span class="event-type-badge badge-${ev.event_type}">${ev.event_type}</span>
                <span class="event-time">${ev.timestamp.split('T')[1].slice(0, 8)}</span>
              </div>
              <div class="event-body">${escapeHtml(payloadStr)}</div>
            </div>
          `;
        }).join('');

        if (document.getElementById('chkAutoScroll').checked) {
          container.scrollTop = container.scrollHeight;
        }
      } catch (err) {
        console.error("Events error:", err);
      }
    }

    async function fetchDiff() {
      try {
        const res = await fetch('/api/diff');
        const text = await res.text();
        const container = document.getElementById('diffContent');
        if (!text || text.trim() === '') {
          container.innerHTML = '<span style="color: var(--text-muted);">(No uncommitted diffs in current worktree)</span>';
          return;
        }

        const lines = text.split('\\n').map(line => {
          if (line.startsWith('+') && !line.startsWith('+++')) return `<span class="diff-add">${escapeHtml(line)}</span>`;
          if (line.startsWith('-') && !line.startsWith('---')) return `<span class="diff-del">${escapeHtml(line)}</span>`;
          if (line.startsWith('diff --git') || line.startsWith('@@')) return `<span class="diff-header">${escapeHtml(line)}</span>`;
          return escapeHtml(line);
        });
        container.innerHTML = lines.join('\\n');
      } catch (err) {
        console.error("Diff load error:", err);
      }
    }

    async function sendSteer() {
      const txt = document.getElementById('txtSteer');
      const val = txt.value.trim();
      if (!val) return;

      try {
        const res = await fetch('/api/steer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ hint: val })
        });
        if (res.ok) {
          txt.value = '';
          alert('✓ Steering hint sent to active session!');
          fetchData();
        } else {
          alert('Error sending hint');
        }
      } catch (err) {
        alert('Network error: ' + err);
      }
    }

    async function togglePause() {
      try {
        const res = await fetch('/api/steer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ paused: !isPaused })
        });
        if (res.ok) {
          fetchData();
        }
      } catch (err) {
        alert('Error toggling pause: ' + err);
      }
    }

    function escapeHtml(str) {
      return (str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    // Auto-refresh every 2.5 seconds
    fetchData();
    fetchDiff();
    setInterval(fetchData, 2500);
    setInterval(fetchDiff, 6000);
  </script>
</body>
</html>
"""


class DashboardRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy HTTP request logging to stderr
        return

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/" or path == "/index.html":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
            return

        if path == "/api/status":
            self._handle_status()
            return

        if path == "/api/events":
            self._handle_events(parsed_url.query)
            return

        if path == "/api/prd":
            self._handle_prd()
            return

        if path == "/api/diff":
            self._handle_diff()
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/api/steer":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                return

            session_dir = self._resolve_current_session_dir()
            if not session_dir:
                self.send_error(
                    HTTPStatus.SERVICE_UNAVAILABLE, "No active session found"
                )
                return

            if "hint" in data:
                submit_steer_hint(session_dir, data["hint"])

            if "paused" in data:
                set_paused_state(session_dir, bool(data["paused"]))

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _resolve_current_session_dir(self) -> str:
        sessions = find_all_sessions()
        if sessions:
            return sessions[0]["session_dir"]
        default_dir = os.path.join(get_sessions_dir(), "default-session")
        os.makedirs(default_dir, exist_ok=True)
        return default_dir

    def _handle_status(self) -> None:
        session_dir = self._resolve_current_session_dir()
        session_id = os.path.basename(session_dir) if session_dir else ""
        paused = is_session_paused(session_dir) if session_dir else False
        events = read_events(session_dir) if session_dir else []

        current_story = ""
        story_status = ""
        slot = 1
        pid = ""

        # Check latest slot metadata
        meta_file = os.path.join(BOT_DIR, "data", "runs", "slot-1.json")
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                current_story = meta.get("storyId", "")
                pid = str(meta.get("pid", ""))
            except (json.JSONDecodeError, OSError):
                pass

        # Find status from PRD
        prd_file = os.path.join(BOT_DIR, "data", "prd.json")
        if os.path.exists(prd_file) and current_story:
            try:
                with open(prd_file, "r", encoding="utf-8") as f:
                    prd = json.load(f)
                for s in prd.get("stories", []):
                    if s.get("id") == current_story:
                        story_status = s.get("status", "")
                        break
            except (json.JSONDecodeError, OSError):
                pass

        data = {
            "session_id": session_id,
            "session_dir": session_dir,
            "is_paused": paused,
            "current_story": current_story,
            "story_status": story_status,
            "slot": slot,
            "pid": pid,
            "event_count": len(events),
        }

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def _handle_events(self, query_string: str) -> None:
        session_dir = self._resolve_current_session_dir()
        events = read_events(session_dir, limit=100) if session_dir else []
        event_dicts = [ev.to_dict() for ev in events]

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(event_dicts).encode("utf-8"))

    def _handle_prd(self) -> None:
        prd_file = os.path.join(BOT_DIR, "data", "prd.json")
        if not os.path.exists(prd_file):
            prd_file = os.path.join(BOT_DIR, "data", "prd.example.json")

        stories = []
        if os.path.exists(prd_file):
            try:
                with open(prd_file, "r", encoding="utf-8") as f:
                    prd = json.load(f)
                stories = prd.get("stories", [])
            except (json.JSONDecodeError, OSError):
                pass

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"stories": stories}).encode("utf-8"))

    def _handle_diff(self) -> None:
        diff_output = ""
        try:
            # Run git diff against HEAD in target repo or bot dir
            res = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=BOT_DIR,
                capture_output=True,
                text=True,
                timeout=5,
            )
            diff_output = res.stdout[:50000]  # cap at 50KB
        except (subprocess.SubprocessError, OSError):
            diff_output = ""

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(diff_output.encode("utf-8"))


def run_dashboard(
    host: str = "127.0.0.1", port: int = 8501, open_browser: bool = False
):
    server = ThreadingHTTPServer((host, port), DashboardRequestHandler)
    url = f"http://{host}:{port}/"
    print("============================================================")
    print(f"  brave-dev-loop Live Control Plane running at: {url}")
    print("  Press Ctrl+C to stop.")
    print("============================================================")

    if open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="brave-dev-loop Web Dashboard")
    parser.add_argument(
        "--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8501,
        help="Port to bind (default: 8501)",
    )
    parser.add_argument(
        "--open", action="store_true", help="Open browser automatically"
    )

    args = parser.parse_args()
    run_dashboard(host=args.host, port=args.port, open_browser=args.open)


if __name__ == "__main__":
    main()
