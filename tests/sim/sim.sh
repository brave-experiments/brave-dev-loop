#!/bin/bash
# A throwaway bot deployment for exercising concurrent runs by hand.
#
# Builds a copy of this repo in /tmp with a fake agent binary, a fake target
# repo, and a PRD full of dummy stories — so `./run.sh` can be run for real,
# several times at once, without spending tokens or touching anything.
#
#   tests/sim/sim.sh build          # create it (also resets)
#   tests/sim/sim.sh reset          # kill runs, clear logs/state, reseed PRD
#   tests/sim/sim.sh run 2 2        # 2 concurrent runs, 2 iterations each
#   tests/sim/sim.sh status
#   tests/sim/sim.sh clean          # delete it
#
# The simulation lives in /tmp/botsim; nothing here touches the real bot
# directory's data or logs.

set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SIM="${BOTSIM_DIR:-/tmp/botsim}"
TARGET="$SIM-target"
STORIES="${BOTSIM_STORIES:-6}"
AGENT_SECONDS="${BOTSIM_AGENT_SECONDS:-6}"

kill_runs() {
  pkill -9 -f "$SIM/run.sh" 2>/dev/null || true
  pkill -9 -f "$SIM/fake-claude.sh" 2>/dev/null || true
  pkill -9 -f "tee -a $SIM/logs" 2>/dev/null || true
  sleep 1
}

seed_prd() {
  python3 - "$SIM" "$STORIES" <<'PY'
import json, sys
sim, n = sys.argv[1], int(sys.argv[2])
stories = [
    {"id": f"US-{i:03d}", "title": f"Story {i}", "status": "pending",
     "priority": i, "description": f"dummy story {i} — resolve issue #{100 + i}",
     "acceptanceCriteria": ["do the thing"]}
    for i in range(1, n + 1)
]
json.dump({"projectName": "sim", "stories": stories},
          open(f"{sim}/data/prd.json", "w"), indent=2)
PY
}

reset() {
  kill_runs
  rm -rf "$SIM/logs" "$SIM/data/runs" "$SIM/data/claims.json" \
         "$SIM"/.run.lock "$SIM"/.run.lock.d "$SIM"/.run.slot-*.lock \
         "$SIM"/data/run-state.slot-*.json "$SIM"/data/progress.txt \
         "$SIM"/*.log 2>/dev/null || true
  mkdir -p "$SIM/logs" "$SIM/data"
  "$REPO/scripts/reset-run-state.sh" --state-file "$SIM/data/run-state.json" --quiet
  seed_prd
  echo "sim reset: $SIM ($STORIES stories)"
}

build() {
  rm -rf "$SIM" "$TARGET"
  mkdir -p "$SIM"
  # Copy the working tree, not a git checkout: the point is to test what is
  # on disk right now. Lock files are excluded — a copied lock directory
  # names a pid that is alive in the real deployment, and the copy would
  # look permanently busy.
  tar -cf - -C "$REPO" \
    --exclude .git --exclude logs --exclude data --exclude .ignore \
    --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache \
    --exclude '.run.lock' --exclude '.run.lock.d' --exclude '.run.slot-*' \
    . | tar -xf - -C "$SIM"
  mkdir -p "$SIM/data" "$SIM/logs" "$SIM/.ignore"
  echo "someorgmember" > "$SIM/.ignore/org-members.txt"

  git init -q -b main "$TARGET"
  git -C "$TARGET" config user.email sim@example.com
  git -C "$TARGET" config user.name sim
  echo "sim target" > "$TARGET/README.md"
  git -C "$TARGET" add -A
  git -C "$TARGET" commit -qm "init"

  cat > "$SIM/config.json" <<EOF
{
  "project": {
    "name": "sim", "org": "example", "prRepository": "example/sim",
    "issueRepository": "example/sim", "defaultBranch": "main",
    "targetRepoPath": "$TARGET", "useFork": false,
    "profile": "bravebot", "prdMode": "curated"
  },
  "bot": {
    "username": "simbot", "email": "sim@example.com", "sshKeyPath": null,
    "ghAccount": null, "ghConfigDir": null, "agent": "claude",
    "maxConcurrentRuns": ${BOTSIM_SLOTS:-2},
    "claudeModel": null, "claudeBin": "$SIM/fake-claude.sh",
    "codexModel": null, "codexBin": null, "cursorModel": null, "cursorBin": null
  },
  "labels": {"prLabels": [], "issueLabels": [], "disabledTestLabel": ""},
  "bestPractices": {"docsDir": "docs", "indexFile": "b.md", "securityFile": "S.md"},
  "schedules": {"syncRepo": false, "syncRepoPath": null}
}
EOF

  cat > "$SIM/fake-claude.sh" <<EOF
#!/bin/bash
# Stands in for the agent: says what it was asked to do, then "works".
prompt="\${@: -1}"
story=\$(printf '%s' "\$prompt" | grep -o 'story US-[0-9]*' | head -1)
echo "{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"text\",\"text\":\"working on \$story in slot \$BOT_RUN_SLOT\"}]}}"
sleep $AGENT_SECONDS
EOF
  chmod +x "$SIM/fake-claude.sh"

  reset
  echo "sim built: $SIM (target $TARGET)"
}

sync_scripts() {
  cp "$REPO/run.sh" "$SIM/run.sh"
  cp -R "$REPO/scripts/." "$SIM/scripts/"
  rm -rf "$SIM/scripts/lib/__pycache__"
}

case "${1:-}" in
  build) build ;;
  reset) sync_scripts; reset ;;
  sync) sync_scripts; echo "scripts synced into $SIM" ;;
  clean) kill_runs; rm -rf "$SIM" "$TARGET"; echo "sim removed" ;;
  status) (cd "$SIM" && ./run.sh --status) ;;
  run)
    runs="${2:-2}"; iters="${3:-2}"
    sync_scripts; reset
    for i in $(seq 1 "$runs"); do
      (cd "$SIM" && ./run.sh "$iters" > "$SIM/run-$i.log" 2>&1) &
      sleep 0.5
    done
    wait
    for i in $(seq 1 "$runs"); do
      echo "-- run $i: $(grep -h 'Run slot' "$SIM/run-$i.log" | head -1)"
      grep -h "  Story:" "$SIM/run-$i.log" | sed 's/^ *//; s/^/     /'
    done
    ;;
  *) sed -n '2,20p' "$0"; exit 1 ;;
esac
