#!/usr/bin/env bash
# 临时脚本：assessment 页 key 消费扫描（提交前删除）
cd /home/invincible/daily/vibecoding/Agent_Develop/Edu_Agent/frontend || exit 1
echo "== SummaryCard"
grep -oE 'tr\((`|"'"'"')[a-zA-Z_.]+' src/components/pages/assessment/SummaryCard.tsx | sort -u
echo "== assessment page"
grep -oE 'tr\((`|"'"'"')[a-zA-Z_.]+' 'src/app/(workspace)/assessment/page.tsx' | sort -u
echo "== ConfigCard"
grep -oE 'tr\((`|"'"'"')[a-zA-Z_.]+' src/components/pages/assessment/ConfigCard.tsx | sort -u
echo "== QuestionCard"
grep -oE 'tr\((`|"'"'"')[a-zA-Z_.]+' src/components/pages/assessment/QuestionCard.tsx | sort -u
echo "== FeedbackCard"
grep -oE 'tr\((`|"'"'"')[a-zA-Z_.]+' src/components/pages/assessment/FeedbackCard.tsx | sort -u
