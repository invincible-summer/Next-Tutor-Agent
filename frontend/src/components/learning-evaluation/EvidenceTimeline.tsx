"use client";
// 证据时间线 + 单条证据详情 Drawer（plan §14.2/§14.3/§14.6）：
// - 行级：时间 / 来源 / 涉及概念 / 反馈摘要，[看这条依据] 打开详情。
// - 详情：原始作答、题目公开/揭晓视图、帮助事件、解释主张（含 RBT
//   过程标签）、本题结果、复核记录。
// - 复议入口走 C9（评价不准确 → 具体问题分类）；删除走 lifecycle
//   （物理清除来源副本、失效重综合），不用空按钮。
import { useCallback, useEffect, useState } from "react";
import { FileQuestion, MessagesSquare, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Drawer } from "@/components/ui/Drawer";
import { Modal, ConfirmModal } from "@/components/ui/Modal";
import { EmptyState } from "@/components/ui/EmptyState";
import { et, type Lang } from "@/lib/evaluation-labels";
import { verdictTone } from "@/lib/labels";
import {
  createEvalReview,
  deleteEvalEvidence,
  getEvalEvidenceDetail,
} from "@/lib/api-modules";
import { useEvaluationCacheStore } from "@/lib/store";
import type { EvalEvidenceDetail, EvalSourceTimelineItem } from "@/lib/types-modules";

const T = {
  zh: {
    kind: "来源",
    evidence: "看这条依据",
    empty: "尚无学习证据",
    emptyDesc: "完成一次对话讲解或习题作答后，这里会出现对应证据。",
    review: "复核中",
    decided: {
      uphold: "复核维持原判读",
      revise: "复核修正判读",
      invalidate: "复核撤销判读",
      insufficient_evidence: "证据不足·待再核对",
    },
    deleted: "材料已删除",
    raw: "你的原始表现",
    task: "题目",
    assistance: "帮助条件",
    revealed: "答案与讲解",
    interpretation: "这次观察的解释",
    result: "本题结果",
    reviews: "复核记录",
    processes: "实际表现方式",
    dispute: "对这条评价有异议",
    delete: "删除这条证据",
    disputeTitle: "评价不准确",
    disputeDesc: "请选择或描述具体问题；提交后会触发人工/模型复核。",
    issue_question: "题目有错误",
    issue_misjudge: "判读不当",
    issue_assistance: "帮助记录有误",
    issue_other: "其他问题",
    reason: "具体说明",
    reasonPh: "哪里不准确？发生了什么？",
    submit: "提交复核",
    cancel: "取消",
    submitted: "已提交复核请求",
    deleteTitle: "删除这条学习证据",
    deleteDesc:
      "将物理清除这条证据的原始材料并重新核对该概念的评价（不可恢复）。删除对话不会自动到这里，需单独确认。",
    confirm: "确认删除",
    deletedToast: "证据已删除",
    loadFail: "证据详情加载失败",
    retry: "重试",
    noInterpretation: "该证据尚未完成评价。",
    claimLimits: "还不能说明",
  },
  en: {
    kind: "Source",
    evidence: "View evidence",
    empty: "No learning evidence yet",
    emptyDesc: "Evidence appears here after a dialogue explanation or an assessment answer.",
    review: "Under review",
    decided: {
      uphold: "Review upheld",
      revise: "Review revised",
      invalidate: "Review invalidated",
      insufficient_evidence: "Insufficient evidence",
    },
    deleted: "Material deleted",
    raw: "Your original response",
    task: "Question",
    assistance: "Help conditions",
    revealed: "Answer & explanation",
    interpretation: "Interpretation of this observation",
    result: "This item's result",
    reviews: "Review records",
    processes: "Observed processes",
    dispute: "Dispute this evaluation",
    delete: "Delete this evidence",
    disputeTitle: "Inaccurate evaluation",
    disputeDesc: "Pick or describe the specific problem; this triggers a review.",
    issue_question: "Question is flawed",
    issue_misjudge: "Misjudged",
    issue_assistance: "Help record is wrong",
    issue_other: "Other issue",
    reason: "Details",
    reasonPh: "What is inaccurate, and what happened?",
    submit: "Submit review",
    cancel: "Cancel",
    submitted: "Review requested",
    deleteTitle: "Delete this learning evidence",
    deleteDesc:
      "This physically removes the source material and re-checks the concept's evaluation. Not recoverable.",
    confirm: "Delete",
    deletedToast: "Evidence deleted",
    loadFail: "Failed to load evidence",
    retry: "Retry",
    noInterpretation: "This evidence has not been evaluated yet.",
    claimLimits: "Not yet shown",
  },
} as const;

function fmtIso(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 证据详情 Drawer：原始材料 + 揭晓视图 + 解释主张 + 复核/删除。 */
export function EvidenceDetailDrawer({
  sourceId,
  onClose,
  lang,
  onMutated,
}: {
  sourceId: string | null;
  onClose: () => void;
  lang: Lang;
  /** 删除成功后通知容器刷新时间线/缓存。 */
  onMutated?: () => void;
}) {
  const t = T[lang === "en" ? "en" : "zh"];
  const [detail, setDetail] = useState<EvalEvidenceDetail | null>(null);
  const [error, setError] = useState(false);
  const [disputeOpen, setDisputeOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [issueKind, setIssueKind] = useState("other");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const load = useCallback(() => {
    if (!sourceId) return;
    getEvalEvidenceDetail(sourceId)
      .then((d) => {
        setDetail(d);
        setError(false);
      })
      .catch(() => {
        setDetail(null);
        setError(true);
      });
  }, [sourceId]);

  // 换证据源时在渲染期收起弹窗（derive-state 模式，避免 effect 内同步 setState）。
  const [prevSourceId, setPrevSourceId] = useState(sourceId);
  if (sourceId !== prevSourceId) {
    setPrevSourceId(sourceId);
    setDisputeOpen(false);
    setDeleteOpen(false);
    setNotice("");
  }

  useEffect(() => {
    load();
  }, [load]);

  const submitReview = async () => {
    if (!detail || reason.trim().length < 4 || busy) return;
    setBusy(true);
    try {
      await createEvalReview(detail.source_id, {
        interpretation_id: detail.interpretation_id || detail.reviews[0]?.interpretation_id || "",
        reason: reason.trim(),
        issue_kind: issueKind,
        expected_revision: (detail as EvalEvidenceDetail & { source_revision?: number })
          .source_revision || 1,
      });
      setNotice(t.submitted);
      setDisputeOpen(false);
      setReason("");
      load();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async () => {
    if (!detail || busy) return;
    setBusy(true);
    try {
      await deleteEvalEvidence(detail.source_id);
      useEvaluationCacheStore.getState().invalidate("");
      setNotice(t.deletedToast);
      setDeleteOpen(false);
      onMutated?.();
      onClose();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };

  const interpClaims =
    (detail?.interpretation?.observation_claims || []) as Array<
      Record<string, unknown>
    >;

  return (
    <Drawer open={!!sourceId} onClose={onClose} title={t.interpretation} width={520}>
      {error ? (
        <EmptyState
          title={t.loadFail}
          desc=""
          action={<Button size="sm" onClick={load}>{t.retry}</Button>}
        />
      ) : !detail ? (
        <p className="py-4 text-xs text-muted">…</p>
      ) : (
        <div className="space-y-3">
          {notice && (
            <p aria-live="polite" className="rounded-[8px] bg-success/10 px-3 py-2 text-xs text-success">
              {notice}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone="info">
              {detail.kind === "dialogue" ? <MessagesSquare size={11} className="mr-1" /> : <FileQuestion size={11} className="mr-1" />}
              {et(lang, `eval.source.${detail.kind || "dialogue"}`)}
            </Badge>
            <span className="tnum text-[0.66rem] text-muted">{fmtIso(detail.observed_at)}</span>
            {detail.availability !== "available" && (
              <Badge tone="muted">{et(lang, `eval.avail.${detail.availability}`)}</Badge>
            )}
          </div>

          {detail.canonical_text && (
            <section>
              <p className="mb-1 text-[0.7rem] font-medium text-fg-secondary">{t.raw}</p>
              <pre className="max-h-40 overflow-y-auto rounded-[8px] bg-surface-sunken p-2.5 text-[0.72rem] leading-relaxed whitespace-pre-wrap text-fg">
                {detail.canonical_text}
              </pre>
            </section>
          )}

          {detail.task && (
            <section>
              <p className="mb-1 text-[0.7rem] font-medium text-fg-secondary">{t.task}</p>
              <div className="rounded-[8px] border border-border-light bg-surface px-2.5 py-2 text-xs leading-relaxed text-fg">
                <p className="whitespace-pre-wrap">{detail.task.stem}</p>
                {Object.keys(detail.task.options || {}).length > 0 && (
                  <ul className="mt-1 space-y-0.5 text-fg-secondary">
                    {Object.entries(detail.task.options).map(([k, v]) => (
                      <li key={k}>{k}. {v}</li>
                    ))}
                  </ul>
                )}
              </div>
            </section>
          )}

          {detail.assistance?.length > 0 && (
            <section>
              <p className="mb-1 text-[0.7rem] font-medium text-fg-secondary">{t.assistance}</p>
              <ul className="space-y-0.5">
                {detail.assistance.map((a, i) => (
                  <li key={i} className="text-[0.72rem] text-fg-secondary">
                    · {et(lang, `eval.hint.${a.kind}`)}
                    {a.detail ? `：${a.detail}` : ""}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {detail.revealed && (
            <section>
              <p className="mb-1 text-[0.7rem] font-medium text-fg-secondary">{t.revealed}</p>
              <div className="rounded-[8px] border border-border-light bg-surface px-2.5 py-2 text-xs leading-relaxed text-fg-secondary">
                <p><span className="font-medium text-fg">{lang === "en" ? "Answer: " : "答案："}</span>{detail.revealed.answer}</p>
                {detail.revealed.explanation && (
                  <p className="mt-1 whitespace-pre-wrap">{detail.revealed.explanation}</p>
                )}
              </div>
            </section>
          )}

          {detail.task_result?.verdict && (
            <section>
              <p className="mb-1 text-[0.7rem] font-medium text-fg-secondary">{t.result}</p>
              <Badge tone={verdictTone(detail.task_result.verdict)}>
                {lang === "en" ? detail.task_result.verdict : detail.task_result.verdict === "correct" ? "回答正确" : detail.task_result.verdict === "partial" ? "部分正确" : "回答错误"}
              </Badge>
            </section>
          )}

          {interpClaims.length > 0 && (
            <section>
              <p className="mb-1 text-[0.7rem] font-medium text-fg-secondary">{t.interpretation}</p>
              <ul className="space-y-1.5">
                {interpClaims.map((c, i) => {
                  const processes = (c.cognitive_processes as string[] | undefined) || [];
                  return (
                    <li key={i} className="rounded-[8px] border border-border-light bg-surface px-2.5 py-2">
                      <p className="text-xs leading-relaxed text-fg">
                        {String(c.statement || "")}
                      </p>
                      {processes.length > 0 && (
                        <div className="mt-1 flex flex-wrap items-center gap-1.5">
                          <span className="text-[0.66rem] text-muted">{t.processes}:</span>
                          {processes.map((p) => (
                            <Badge key={p} tone="outline">
                              {et(lang, `eval.process.${p}`)}
                            </Badge>
                          ))}
                        </div>
                      )}
                      {Array.isArray(c.limits) && (c.limits as string[]).length > 0 && (
                        <p className="mt-1 text-[0.7rem] text-muted">
                          {t.claimLimits}：{(c.limits as string[]).join("；")}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          )}

          {detail.interpretation?.feedback && (
            <p className="rounded-[8px] bg-info/8 px-3 py-2 text-xs leading-relaxed text-info">
              {detail.interpretation.feedback}
            </p>
          )}

          {detail.reviews.length > 0 && (
            <section>
              <p className="mb-1 text-[0.7rem] font-medium text-fg-secondary">{t.reviews}</p>
              <ul className="space-y-1">
                {detail.reviews.map((r) => {
                  // R24：展示真实决定与时间——"复核中"只用于仍在途的复核
                  const kind = (r.decided_kind || "").trim();
                  const label = kind && t.decided[kind as keyof typeof t.decided];
                  return (
                    <li key={r.review_id} className="rounded-[8px] border border-border-light bg-surface px-2.5 py-2 text-[0.72rem] leading-relaxed text-fg-secondary">
                      <Badge tone={label ? "success" : "info"} className="mr-1.5">
                        {label || t.review}
                      </Badge>
                      {r.reason}
                      {label && r.decided_at && (
                        <span className="ml-1 text-muted">
                          {new Date(r.decided_at).toLocaleDateString()}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          )}

          {!detail.interpretation && !detail.revealed && (
            <p className="text-[0.72rem] text-muted">{t.noInterpretation}</p>
          )}

          <div className="flex flex-wrap items-center gap-2 border-t border-border-light pt-3">
            <Button size="sm" variant="outline" onClick={() => setDisputeOpen(true)}>
              {t.dispute}
            </Button>
            <Button size="sm" variant="danger" onClick={() => setDeleteOpen(true)}>
              <Trash2 size={12} className="mr-1" />
              {t.delete}
            </Button>
          </div>
        </div>
      )}

      <Modal
        open={disputeOpen}
        onClose={() => setDisputeOpen(false)}
        title={t.disputeTitle}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setDisputeOpen(false)}>
              {t.cancel}
            </Button>
            <Button size="sm" disabled={reason.trim().length < 4 || busy} onClick={() => void submitReview()}>
              {t.submit}
            </Button>
          </>
        }
      >
        <p className="mb-3 text-xs leading-relaxed text-muted">{t.disputeDesc}</p>
        <div className="mb-3 flex flex-wrap gap-1.5">
          {(["issue_question", "issue_misjudge", "issue_assistance", "issue_other"] as const).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() =>
                setIssueKind(k === "issue_question" ? "question_error" : k === "issue_misjudge" ? "misjudgment" : k === "issue_assistance" ? "assistance_record" : "other")
              }
              className={`cursor-pointer rounded-full border px-2.5 py-1 text-xs transition-colors ${
                (k === "issue_question" && issueKind === "question_error") ||
                (k === "issue_misjudge" && issueKind === "misjudgment") ||
                (k === "issue_assistance" && issueKind === "assistance_record") ||
                (k === "issue_other" && issueKind === "other")
                  ? "border-accent bg-accent-soft text-accent-strong"
                  : "border-border text-fg-secondary hover:border-accent"
              }`}
            >
              {t[k]}
            </button>
          ))}
        </div>
        <label className="block text-xs font-medium text-fg-secondary">
          {t.reason}
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder={t.reasonPh}
            rows={4}
            maxLength={1200}
            className="mt-1 w-full rounded-[8px] border border-border bg-surface px-2.5 py-2 text-xs text-fg"
          />
        </label>
      </Modal>

      <ConfirmModal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => void doDelete()}
        title={t.deleteTitle}
        desc={t.deleteDesc}
        confirmText={t.confirm}
        cancelText={t.cancel}
      />
    </Drawer>
  );
}

/** 证据时间线：行级摘要 + 打开详情 Drawer。 */
export function EvidenceTimeline({
  items,
  lang,
  conceptNames,
}: {
  items: EvalSourceTimelineItem[];
  lang: Lang;
  /** concept_refs（概念 key/id）→ 展示名（页面按图谱/概念表提供）。 */
  conceptNames?: (ref: string) => string;
}) {
  const t = T[lang === "en" ? "en" : "zh"];
  const [openSource, setOpenSource] = useState<string | null>(null);
  if (!items || items.length === 0) {
    return <EmptyState title={t.empty} desc={t.emptyDesc} />;
  }
  return (
    <>
      <ul className="space-y-2" data-testid="evidence-timeline">
        {items.map((it) => (
          <li key={it.source_id} className="rounded-[10px] border border-border-light bg-surface px-3 py-2.5">
            <div className="mb-1 flex flex-wrap items-center gap-1.5">
              <Badge tone={it.kind === "dialogue" ? "info" : "accent"}>
                {et(lang, `eval.source.${it.kind || "dialogue"}`)}
              </Badge>
              <span className="tnum text-[0.66rem] text-muted">{fmtIso(it.observed_at)}</span>
              {it.review_status === "active" && <Badge tone="warning">{t.review}</Badge>}
              {it.availability === "deleted" && <Badge tone="muted">{t.deleted}</Badge>}
            </div>
            {it.summary && (
              <p className="mb-1.5 line-clamp-3 text-xs leading-relaxed text-fg-secondary">
                {it.summary}
              </p>
            )}
            {it.concept_refs.length > 0 && (
              <p className="mb-1.5 text-[0.7rem] text-muted">
                {lang === "en" ? "Concepts: " : "涉及："}
                {it.concept_refs
                  .slice(0, 4)
                  .map((c) => (conceptNames ? conceptNames(c) : c))
                  .join(lang === "en" ? ", " : "、")}
              </p>
            )}
            <button
              type="button"
              onClick={() => setOpenSource(it.source_id)}
              className="cursor-pointer text-xs text-accent hover:underline"
            >
              {t.evidence}
            </button>
          </li>
        ))}
      </ul>
      <EvidenceDetailDrawer
        sourceId={openSource}
        onClose={() => setOpenSource(null)}
        lang={lang}
      />
    </>
  );
}
