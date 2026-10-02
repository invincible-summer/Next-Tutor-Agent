#!/usr/bin/env bash
# Batch-generate all 21 curriculum seed packs from 考纲/ (M5.6).
# Run from repo root:  bash backend/scripts/build_all_packs.sh
# Each pack: LLM draft -> deterministic validation -> draft written to
# backend/seed_pack_drafts/. Failures are retried once. Human review +
# registration in seed_packs/__init__.py is still required afterwards.
set -u
cd "$(dirname "$0")/.."

# --- resolve python (conda env preferred, same as start.sh) ---
if command -v conda &>/dev/null; then
  eval "$(conda shell.bash hook)" 2>/dev/null
  conda activate edu_agent 2>/dev/null || true
fi
PYTHON_BIN="python"
command -v python &>/dev/null || PYTHON_BIN="python3"

OUTLINE_DIR="../考纲"
LOG_DIR="seed_pack_drafts/logs"
mkdir -p "$LOG_DIR"

# stage|subject|outline_file|keep_pack(optional)
ENTRIES=(
  "小学|语文|小学语文考纲.md|"
  "小学|数学|小学数学考纲.md|"
  "小学|英语|小学英语考纲.md|"
  "初中|语文|初中语文考纲.md|"
  "初中|数学|初中数学考纲.md|"
  "初中|英语|初中英语考纲.md|"
  "初中|物理|初中物理考纲.md|"
  "初中|化学|初中化学考纲.md|pack_junior_chemistry"
  "初中|生物|初中生物考纲.md|pack_junior_biology"
  "初中|历史|初中历史考纲.md|"
  "初中|地理|初中地理考纲.md|"
  "初中|政治|初中道德与法治考纲.md|"
  "高中|语文|高中语文考纲.md|"
  "高中|数学|高中数学考纲.md|pack_senior_math"
  "高中|英语|高中英语考纲.md|"
  "高中|物理|高中物理考纲.md|pack_senior_physics"
  "高中|化学|高中化学考纲.md|"
  "高中|生物|高中生物考纲.md|"
  "高中|历史|高中历史考纲.md|"
  "高中|地理|高中地理考纲.md|"
  "高中|政治|高中思想政治.md|"
)

pass=0; fail=0; failed=()
for entry in "${ENTRIES[@]}"; do
  IFS='|' read -r stage subject file keep <<< "$entry"
  name="${stage}${subject}"
  log="$LOG_DIR/${name}.log"
  args=(--stage "$stage" --subject "$subject" --outline "$OUTLINE_DIR/$file" --max-nodes 70)
  [ -n "$keep" ] && args+=(--keep "$keep")
  echo "=== $name (keep=${keep:-none}) ==="
  if "$PYTHON_BIN" scripts/build_seed_pack.py "${args[@]}" >"$log" 2>&1; then
    pass=$((pass+1)); echo "  OK"
  else
    echo "  first attempt failed, retrying once..."
    if "$PYTHON_BIN" scripts/build_seed_pack.py "${args[@]}" >>"$log" 2>&1; then
      pass=$((pass+1)); echo "  OK (retry)"
    else
      fail=$((fail+1)); failed+=("$name"); echo "  FAILED (see $log)"
    fi
  fi
done

echo "=================================="
echo "pass=$pass fail=$fail"
[ "$fail" -gt 0 ] && echo "failed: ${failed[*]}"
