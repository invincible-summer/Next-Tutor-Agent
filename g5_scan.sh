#!/bin/bash
# §19.1 token scan（backend/app + frontend/src 全量，不过滤注释，逐条人工分类）。
cd /home/invincible/daily/vibecoding/Agent_Develop/Edu_Agent || exit 1
TOKENS="p_known MasteryTracker ConceptState record_quiz_result rebuild_mastery derive_concept_status current_mastery mastery_map before_mastery after_mastery learning_gain avg_learning_gain target_mastery planned_mastery mastered_ratio seed_from_mastery KnowledgeNodeMastery MasteryResp masteryColor allow_mastery_update max_confidence"
for tok in $TOKENS; do
  hits=$(grep -rn "$tok" --include='*.py' backend/app 2>/dev/null | grep -v __pycache__ | wc -l)
  hits2=$(grep -rn "$tok" --include='*.ts' --include='*.tsx' frontend/src 2>/dev/null | wc -l)
  total=$((hits + hits2))
  if [ "$total" -gt 0 ]; then
    echo "== $tok: backend=$hits frontend=$hits2"
    grep -rn "$tok" --include='*.py' backend/app 2>/dev/null | grep -v __pycache__ | head -3
    grep -rn "$tok" --include='*.ts' --include='*.tsx' frontend/src 2>/dev/null | head -3
  fi
done
echo "== old routes"
grep -rnE '/student/(mastery|evidence-profile|bloom-profile|learning-records)' --include='*.py' backend/app 2>/dev/null | grep -v __pycache__ | head -3
grep -rnE '/student/(mastery|evidence-profile|bloom-profile|learning-records)' --include='*.ts' --include='*.tsx' frontend/src 2>/dev/null | head -3
echo SCAN_DONE
