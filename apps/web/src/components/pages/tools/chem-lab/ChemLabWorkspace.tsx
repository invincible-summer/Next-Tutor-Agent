"use client";

/**
 * Chem-lab workspace — composition root. Owns URL-driven navigation
 * (?experiment= preparation, ?session= live bench, &at= historical branch
 * offer), the session controller, selection/operation state and every
 * overlay. All chemistry decisions stay in the engine + server; this file
 * only wires intents to the command queue and renders derived state.
 */
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { ConfirmModal, Modal } from "@/components/ui/Modal";
import { Drawer } from "@/components/ui/Drawer";
import { Tabs } from "@/components/ui/Tabs";
import { EmptyState } from "@/components/ui/EmptyState";
import { useUIStore } from "@/lib/store";
import { useAuthStore } from "@/lib/auth-store";
import { makePageT } from "@/lib/i18n-page";
import { DEMO_MODE } from "@/lib/demo";
import { useAssistantPage } from "@/lib/assistant/useAssistantPage";
import { currentRouteEpoch } from "@/lib/assistant/page-context";
import {
  ChemLabToolError,
  createChemLabCheckpoint,
  createChemLabSession,
  deleteChemLabSession,
  finishChemLabSession,
  forkChemLabSession,
  getChemLabEnginePack,
  getChemLabExperiment,
  getChemLabEvents,
  getChemLabRevision,
  getChemLabSession,
  getChemLabCatalog,
  listChemLabSessions,
  resetChemLabSession,
  type ChemLabEnginePack,
  type ChemLabEvent,
  type ChemLabRevisionView,
  type ChemLabExperimentDetail,
  type ChemLabExperimentSummary,
  type ChemLabObservation,
  type ChemLabResultCard,
  type ChemLabSessionSummary,
  type LabCommand,
} from "@/lib/api-chem-lab";
import { BackMark, BusyMark } from "../ToolMarks";
import { STRINGS } from "@/app/(workspace)/tools/lab/chemistry/strings";
import { useChemLabSession, type ChemLabDisplay } from "./useChemLabSession";
import { LabStage } from "./LabStage";
import { useLabPresentation } from "./scene/useLabPresentation";
import { EquipmentTray } from "./EquipmentTray";
import { ReagentPalette } from "./ReagentPalette";
import { OperationToolbar } from "./OperationToolbar";
import { MeasurementPopover } from "./MeasurementPopover";
import { GuidanceRail } from "./GuidanceRail";
import { ObservationLog } from "./ObservationLog";
import { EvidenceDrawer } from "./EvidenceDrawer";
import { LabTimeline } from "./LabTimeline";
import { BranchCompare, type BranchSide } from "./BranchCompare";
import { ExperimentPicker } from "./ExperimentPicker";
import { PreparationPanel, type ChemLabMode } from "./PreparationPanel";
import type { ChemLabObjectRef, OperationDraft } from "./interaction";

type AnyRecord = Record<string, unknown>;

function l10n(entry: unknown, language: string): string {
  if (entry && typeof entry === "object" && !Array.isArray(entry)) {
    const row = entry as AnyRecord;
    const value = row[language] ?? row.zh ?? row.en;
    if (typeof value === "string") return value;
  }
  return "";
}

export function ChemLabWorkspace() {
  const owner = useAuthStore((s) => s.user?.id ?? "guest");
  return (
    <Suspense fallback={<div className="h-full bg-bg" />}>
      <OwnedChemLabWorkspace key={owner} />
    </Suspense>
  );
}

function OwnedChemLabWorkspace() {
  const lang = useUIStore((s) => s.lang);
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);
  const router = useRouter();
  const query = useSearchParams();
  const experimentId = query.get("experiment") ?? "";
  const sessionId = query.get("session") ?? "";
  const atRevision = query.get("at");
  const language = lang === "en" ? "en" : "zh";
  // `&at=` must be an exact non-negative integer; empty/NaN/fractional values
  // are ignored (no side effects, plan §6.3).
  const atNumber = atRevision ? Number(atRevision) : null;
  const historyActive =
    sessionId !== "" && atNumber !== null && Number.isInteger(atNumber) && atNumber >= 0;

  const controller = useChemLabSession();
  const {
    snapshot, display, syncStatus, pendingCount, conflict, lastRejection, notice,
    attach, detach, send, resync, retryPending, dismissConflict, clearNotice,
    lastTransition,
  } = controller;

  const [experiments, setExperiments] = useState<ChemLabExperimentSummary[]>([]);
  const [sessions, setSessions] = useState<ChemLabSessionSummary[]>([]);
  const [listError, setListError] = useState(false);
  const [detail, setDetail] = useState<ChemLabExperimentDetail | null>(null);
  const [mode, setMode] = useState<ChemLabMode>("guided");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<ChemLabObjectRef | null>(null);
  const [draft, setDraft] = useState<OperationDraft | null>(null);
  const [evidenceObs, setEvidenceObs] = useState<ChemLabObservation | null>(null);
  const [measureId, setMeasureId] = useState<string | null>(null);
  const [resultCard, setResultCard] = useState<ChemLabResultCard | null>(null);
  const [deleting, setDeleting] = useState<ChemLabSessionSummary | null>(null);
  const [resetOpen, setResetOpen] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareSides, setCompareSides] = useState<{ base: BranchSide; branch: BranchSide } | null>(null);
  const [mobilePanel, setMobilePanel] = useState<"" | "equipment" | "guidance" | "observations">("");
  const [railTab, setRailTab] = useState("guidance");
  const [enginePack, setEnginePack] = useState<ChemLabEnginePack | null>(null);
  // Historical read-only view (&at=): independent source, generation and
  // abort lifecycle — never shares tokens with the live session effects.
  const [historyView, setHistoryView] = useState<ChemLabRevisionView | null>(null);
  const [historyError, setHistoryError] = useState("");
  const historyGeneration = useRef(0);
  const detailGeneration = useRef(0);
  const sessionGeneration = useRef(0);
  const measureAnchor = useRef<HTMLElement | null>(null);

  // Presentation-only one-shot motion (no chemistry, no commands; plan §7).
  const motion = useLabPresentation({
    sessionId: snapshot?.session_id ?? null,
    packHash: enginePack?.pack_hash ?? null,
    transition: lastTransition,
    conflict: syncStatus === "conflict" || syncStatus === "offline_preview",
  });

  useAssistantPage({
    context: () => ({ schema_version: 1, route_id: "tools_lab_chemistry", route_epoch: currentRouteEpoch() }),
    clientState: () => ({
      dirty: pendingCount > 0,
      blocking_activity: pendingCount > 0 ? "lab_pending_sync" : "none",
      safe_bottom_px: 24,
    }),
  });

  const requestError = useCallback(
    (exc: unknown) => {
      if (exc instanceof ChemLabToolError) {
        if (exc.code === "chem_lab_disabled") return tr("disabled");
        if (exc.code === "chem_lab_session_missing") return tr("sessionMissing");
        if (exc.code === "chem_lab_experiment_missing") return tr("experimentMissing");
      }
      return tr("loadFailed");
    },
    [tr],
  );

  // ---- catalog + session list ----------------------------------------------
  // 目录与会话列表独立失败：会话接口故障只影响"我的会话"区，不能把实验
  // 目录也一并清空（否则一次 5xx 就让整个工作台看起来"没有实验"）。
  const refreshLists = useCallback(
    async (signal?: AbortSignal) => {
      if (DEMO_MODE) return;
      try {
        const catalog = await getChemLabCatalog(signal);
        if (signal?.aborted) return;
        setExperiments(catalog.experiments);
        setListError(false);
      } catch {
        if (!signal?.aborted) setListError(true);
      }
      try {
        const sessionPage = await listChemLabSessions(null, signal);
        if (signal?.aborted) return;
        setSessions(sessionPage.items);
      } catch {
        // 保留上一次的会话列表；目录不受影响。
      }
    },
    [],
  );

  useEffect(() => {
    const ctl = new AbortController();
    void Promise.resolve().then(() => {
      if (!ctl.signal.aborted) void refreshLists(ctl.signal);
    });
    return () => ctl.abort();
  }, [refreshLists]);

  // ---- experiment detail (preparation view) ---------------------------------
  // generation 守卫必须每个 effect 独立：两个 effect 共用一个计数器时，同一次
  // 挂载里后声明的 session effect 会立刻把计数器顶到更新，detail 请求返回时
  // 守卫误判为"已被新运行取代"，finally 里的 setLoading(false) 被跳过——
  // 准备页永远停在"正在创建会话…"加载态（?experiment= 深链必现）。
  useEffect(() => {
    const ctl = new AbortController();
    const token = ++detailGeneration.current;
    void Promise.resolve().then(async () => {
      if (ctl.signal.aborted) return;
      if (!experimentId || sessionId || DEMO_MODE) {
        setDetail(null);
        return;
      }
      setLoading(true);
      setError("");
      try {
        const value = await getChemLabExperiment(experimentId, null, ctl.signal);
        if (ctl.signal.aborted || detailGeneration.current !== token) return;
        setDetail(value);
        setMode((value.modes.includes("guided") ? "guided" : value.modes[0] ?? "guided") as ChemLabMode);
        setAnswers({});
      } catch (exc) {
        if (!ctl.signal.aborted && detailGeneration.current === token) setError(requestError(exc));
      } finally {
        if (!ctl.signal.aborted && detailGeneration.current === token) setLoading(false);
      }
    });
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [experimentId, sessionId]);

  // ---- live session -----------------------------------------------------------
  useEffect(() => {
    const ctl = new AbortController();
    const token = ++sessionGeneration.current;
    void Promise.resolve().then(async () => {
      if (ctl.signal.aborted) return;
      if (!sessionId || DEMO_MODE) {
        detach();
        setEnginePack(null);
        return;
      }
      setLoading(true);
      setError("");
      setSelected(null);
      setDraft(null);
      setResultCard(null);
      setCompareSides(null);
      try {
        const snap = await getChemLabSession(sessionId, ctl.signal);
        if (ctl.signal.aborted || sessionGeneration.current !== token) return;
        const pack = await getChemLabEnginePack(snap.experiment_id, snap.pack_version, ctl.signal);
        if (ctl.signal.aborted || sessionGeneration.current !== token) return;
        setEnginePack(pack);
        await attach(snap, pack);
        if (sessionGeneration.current !== token) return;
      } catch (exc) {
        if (!ctl.signal.aborted && sessionGeneration.current === token) {
          detach();
          setError(requestError(exc));
        }
      } finally {
        if (!ctl.signal.aborted && sessionGeneration.current === token) setLoading(false);
      }
    });
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // Historical read-only source (&at=N): its own generation counter and
  // AbortController so it can never race the live detail/session effects.
  // The fetched revision must equal the URL or the view is discarded.
  useEffect(() => {
    const revision = atRevision ? Number(atRevision) : null;
    const token = ++historyGeneration.current;
    const ctl = new AbortController();
    void Promise.resolve().then(async () => {
      if (ctl.signal.aborted) return;
      if (!sessionId || revision === null || !Number.isInteger(revision) || revision < 0) {
        setHistoryView(null);
        setHistoryError("");
        return;
      }
      setHistoryError("");
      try {
        const view = await getChemLabRevision(sessionId, revision, ctl.signal);
        if (ctl.signal.aborted || historyGeneration.current !== token) return;
        if (view.revision !== revision) return;
        setHistoryView(view);
      } catch (exc) {
        if (ctl.signal.aborted || historyGeneration.current !== token) return;
        setHistoryView(null);
        setHistoryError(requestError(exc));
      }
    });
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, atRevision]);

  // Escape cancels draft / selection (overlays handle their own Escape).
  useEffect(() => {
    const fn = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (draft) setDraft(null);
      else if (selected) setSelected(null);
    };
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, [draft, selected]);

  useEffect(() => {
    if (notice) {
      const timer = setTimeout(clearNotice, 4000);
      return () => clearTimeout(timer);
    }
  }, [notice, clearNotice]);

  // ---- derived pack lookups -----------------------------------------------------
  const pack = (enginePack?.pack ?? null) as AnyRecord | null;

  const goalTitles = useMemo(() => {
    const map = new Map<string, string>();
    for (const goal of Array.isArray(pack?.goals) ? (pack.goals as AnyRecord[]) : []) {
      map.set(String(goal.id ?? ""), l10n(goal.title, language) || String(goal.id ?? ""));
    }
    return map;
  }, [pack, language]);

  const concepts = useMemo(() => {
    const map = new Map<string, { title: string; body: string }>();
    const defs = (pack?._concepts ?? {}) as AnyRecord;
    for (const [id, concept] of Object.entries(defs)) {
      map.set(id, {
        title: l10n((concept as AnyRecord).title, language) || id,
        body: l10n((concept as AnyRecord).body, language),
      });
    }
    return map;
  }, [pack, language]);

  const vesselName = useCallback(
    (vesselId: string) => {
      const start = (pack?.starting_state ?? {}) as AnyRecord;
      for (const vessel of Array.isArray(start.vessels) ? (start.vessels as AnyRecord[]) : []) {
        if (String(vessel.id ?? "") === vesselId) return l10n(vessel.label, language) || vesselId;
      }
      return vesselId;
    },
    [pack, language],
  );

  const modelScopeText = useMemo(() => l10n(pack?.model_scope, language), [pack, language]);

  // Historical mode renders a synthetic display from the revision view; the
  // scene never mixes live events with a replayed frame (plan §6.3).
  const historyDisplay = useMemo<ChemLabDisplay | null>(() => {
    const view = historyView;
    if (!view) return null;
    return {
      renderFrame: view.render_frame,
      guidance: view.guidance ?? null,
      phase: view.phase,
      revision: view.revision,
      simTimeMs: view.sim_time_ms,
      engineState: {
        vessels: Object.fromEntries(
          Object.entries(view.scene_state.vessels ?? {}).map(([id, vessel]) => [
            id,
            {
              kind: vessel.kind,
              slot: vessel.slot,
              volume_uL: vessel.volume_uL,
              capacity_uL: vessel.capacity_uL,
              temperature_milli_c: vessel.temperature_milli_c,
              mix_permille: vessel.mix_permille,
              heat: vessel.heat,
            },
          ]),
        ),
        equipment: Object.fromEntries(
          Object.entries(view.scene_state.equipment ?? {}).map(([id, item]) => [
            id,
            {
              kind: item.kind,
              slot: item.slot,
              load: { volume_uL: item.load_volume_uL },
              connected: item.connected,
              reading: item.reading,
            },
          ]),
        ),
        held: view.scene_state.held ?? null,
      },
      events: view.recent_events ?? [],
      observations: view.observations ?? [],
      serverRevision: view.tip_revision,
    };
  }, [historyView]);
  const effectiveDisplay = historyActive && historyDisplay ? historyDisplay : display;

  // ---- command handlers ----------------------------------------------------------
  const busy = acting || loading;
  const commandsLocked = syncStatus === "conflict" || syncStatus === "offline_preview" || historyActive;

  const submitCommand = useCallback(
    (command: LabCommand) => {
      if (historyActive) return;
      send(command);
      setDraft(null);
    },
    [historyActive, send],
  );

  const heldId = String(display.engineState?.held ?? "") || null;

  /** Atomic move: one `move` command — the engine validates occupancy. */
  const moveObject = useCallback(
    (objectId: string, slotId: string) => {
      if (commandsLocked) return;
      if (heldId && heldId !== objectId) return;
      const state = (display.engineState ?? {}) as AnyRecord;
      const vessels = (state.vessels ?? {}) as AnyRecord;
      const equipment = (state.equipment ?? {}) as AnyRecord;
      const current = String(
        (vessels[objectId] as AnyRecord)?.slot ?? (equipment[objectId] as AnyRecord)?.slot ?? "",
      );
      if (!current || current === slotId) return; // same slot: no-op, no request
      send({ kind: "move", object_id: objectId, slot_id: slotId });
      setDraft(null);
    },
    [commandsLocked, display.engineState, heldId, send],
  );

  /** Drop a legacy hold without moving: the `release` compatibility command. */
  const releaseHeld = useCallback(() => {
    if (!heldId || commandsLocked) return;
    send({ kind: "release", object_id: heldId });
  }, [commandsLocked, heldId, send]);

  const sessionAction = useCallback(
    async (fn: () => Promise<void>) => {
      if (acting) return;
      setActing(true);
      setError("");
      try {
        await fn();
      } catch (exc) {
        setError(requestError(exc));
      } finally {
        setActing(false);
      }
    },
    [acting, requestError],
  );

  const makeCheckpoint = useCallback(
    (label: string) => {
      if (!snapshot) return;
      void sessionAction(async () => {
        await createChemLabCheckpoint(snapshot.session_id, label);
        const fresh = await getChemLabSession(snapshot.session_id);
        if (enginePack) await attach(fresh, enginePack);
      });
    },
    [snapshot, sessionAction, attach, enginePack],
  );

  const forkSession = useCallback(
    (payload: { checkpoint_id?: string; at_revision?: number }) => {
      if (!snapshot) return;
      void sessionAction(async () => {
        const result = await forkChemLabSession(snapshot.session_id, payload);
        if (enginePack) await attach(result.session, enginePack);
        router.replace(`/tools/lab/chemistry?session=${encodeURIComponent(result.session.session_id)}`);
        void refreshLists();
      });
    },
    [snapshot, sessionAction, attach, router, refreshLists, enginePack],
  );

  /** Leave history mode: drop `at`, stay on the same session. */
  const backToLatest = useCallback(() => {
    const params = new URLSearchParams(query.toString());
    params.delete("at");
    router.replace(`/tools/lab/chemistry?${params.toString()}`);
  }, [query, router]);

  /** Enter / move history mode at one revision (banner + timeline jumps). */
  const jumpRevision = useCallback(
    (revision: number) => {
      const params = new URLSearchParams(query.toString());
      if (sessionId) params.set("session", sessionId);
      params.set("at", String(revision));
      router.replace(`/tools/lab/chemistry?${params.toString()}`);
    },
    [query, router, sessionId],
  );

  const resetSession = useCallback(() => {
    if (!snapshot) return;
    void sessionAction(async () => {
      const result = await resetChemLabSession(snapshot.session_id, {});
      if (enginePack) await attach(result.session, enginePack);
      setResetOpen(false);
      router.replace(`/tools/lab/chemistry?session=${encodeURIComponent(result.session.session_id)}`);
      void refreshLists();
    });
  }, [snapshot, sessionAction, attach, router, refreshLists, enginePack]);

  const finishSession = useCallback(() => {
    if (!snapshot) return;
    void sessionAction(async () => {
      const card = await finishChemLabSession(snapshot.session_id);
      setResultCard(card);
      const fresh = await getChemLabSession(snapshot.session_id);
      if (enginePack) await attach(fresh, enginePack);
      void refreshLists();
    });
  }, [snapshot, sessionAction, attach, refreshLists, enginePack]);

  const deleteSession = useCallback(() => {
    if (!deleting) return;
    void sessionAction(async () => {
      await deleteChemLabSession(deleting.session_id);
      setDeleting(null);
      if (deleting.session_id === sessionId) {
        router.replace("/tools/lab/chemistry");
      }
      void refreshLists();
    });
  }, [deleting, sessionId, sessionAction, router, refreshLists]);

  const startExperiment = useCallback(() => {
    if (!detail || starting) return;
    setStarting(true);
    setError("");
    void (async () => {
      try {
        const snap = await createChemLabSession({
          experiment_id: detail.id,
          pack_version: detail.pack_version,
          mode,
          language,
          session_seed: 0,
        });
        const packNow = await getChemLabEnginePack(snap.experiment_id, snap.pack_version);
        setEnginePack(packNow);
        await attach(snap, packNow);
        router.replace(`/tools/lab/chemistry?session=${encodeURIComponent(snap.session_id)}`);
        void refreshLists();
      } catch (exc) {
        setError(requestError(exc));
      } finally {
        setStarting(false);
      }
    })();
  }, [detail, starting, mode, language, attach, router, requestError, refreshLists]);

  const openCompare = useCallback(() => {
    if (!snapshot) return;
    setCompareOpen(true);
    void sessionAction(async () => {
      const branch = sessions.find(
        (row) => row.session_id !== snapshot.session_id && row.experiment_id === snapshot.experiment_id,
      );
      if (!branch) {
        setCompareSides(null);
        return;
      }
      const [branchSnap, branchEvents] = await Promise.all([
        getChemLabSession(branch.session_id),
        getChemLabEvents(branch.session_id, 0, 200),
      ]);
      const baseEvents = await getChemLabEvents(snapshot.session_id, 0, 200);
      setCompareSides({
        base: {
          label: tr("thisSession"),
          frame: snapshot.render_frame ?? null,
          engineState: (snapshot.engine_state ?? null) as AnyRecord | null,
          events: baseEvents.items,
          revision: snapshot.revision,
          simTimeMs: snapshot.sim_time_ms,
          packHash: snapshot.pack_hash,
        },
        branch: {
          label: l10n(branch.title, language) || branch.session_id,
          frame: branchSnap.render_frame ?? null,
          engineState: (branchSnap.engine_state ?? null) as AnyRecord | null,
          events: branchEvents.items as ChemLabEvent[],
          revision: branchSnap.revision,
          simTimeMs: branchSnap.sim_time_ms,
          packHash: branchSnap.pack_hash,
        },
      });
    });
  }, [snapshot, sessions, sessionAction, language, tr]);

  // ---- banners ---------------------------------------------------------------------
  const banner = (() => {
    if (display.phase === "safety_locked" || syncStatus === "safety_locked") {
      return (
        <div role="alert" className="flex flex-wrap items-center gap-2 border-b border-danger/30 bg-danger/10 px-4 py-2"
          data-testid="chem-lab-safety-banner">
          <span className="text-xs font-medium text-danger">{tr("safetyTitle")}</span>
          <span className="text-[11px] text-fg-secondary">{tr("safetyDesc")}</span>
        </div>
      );
    }
    if (conflict) {
      return (
        <div role="alert" className="flex flex-wrap items-center gap-2 border-b border-[#e0a23c]/40 bg-[#e0a23c]/10 px-4 py-2"
          data-testid="chem-lab-conflict-banner">
          <span className="text-xs font-medium text-fg">{tr("conflictTitle")}</span>
          <span className="text-[11px] text-fg-secondary">{tr("conflictDesc")}</span>
          <Button size="sm" variant="outline" disabled={acting}
            onClick={() => void sessionAction(async () => { await resync(); dismissConflict(); })}>
            {tr("retrySync")}
          </Button>
          <Button size="sm" variant="ghost" onClick={dismissConflict}>{tr("dismiss")}</Button>
        </div>
      );
    }
    if (syncStatus === "offline_preview") {
      return (
        <div role="alert" className="flex flex-wrap items-center gap-2 border-b border-[#e0a23c]/40 bg-[#e0a23c]/10 px-4 py-2"
          data-testid="chem-lab-offline-banner">
          <span className="text-xs font-medium text-fg">{tr("offlineTitle")}</span>
          <span className="text-[11px] text-fg-secondary">{tr("offlineDesc")}</span>
          <Button size="sm" variant="outline" onClick={retryPending}>{tr("retrySync")}</Button>
        </div>
      );
    }
    if (notice) {
      return (
        <div role="status" className="border-b border-accent/25 bg-accent-soft/60 px-4 py-1.5 text-[11px] text-accent-strong">
          {tr(notice)}
        </div>
      );
    }
    return null;
  })();

  // ---- preparation / catalog views ---------------------------------------------------
  if (!sessionId) {
    if (experimentId) {
      return (
        <div className="h-full overflow-y-auto bg-bg" data-testid="chem-lab-prepare-page">
          {loading ? (
            <p role="status" className="py-16 text-center text-sm text-muted">{tr("starting")}</p>
          ) : error ? (
            <div className="mx-auto max-w-md py-16">
              <EmptyState title={error} action={<Button size="sm" variant="outline" onClick={() => router.replace("/tools/lab/chemistry")}>{tr("backToCatalog")}</Button>} />
            </div>
          ) : detail ? (
            <PreparationPanel
              detail={detail}
              language={language}
              mode={mode}
              answers={answers}
              busy={starting || DEMO_MODE}
              tr={tr}
              onModeChange={setMode}
              onAnswer={(id, option) => setAnswers((prev) => ({ ...prev, [id]: option }))}
              onStart={startExperiment}
              onBack={() => router.replace("/tools/lab/chemistry")}
            />
          ) : null}
          {error && detail ? <p role="alert" className="px-8 pb-6 text-xs text-danger">{error}</p> : null}
        </div>
      );
    }
    return (
      <div className="h-full overflow-y-auto bg-bg px-5 py-6 sm:px-8" data-testid="chem-lab-catalog-page">
        <div className="mx-auto max-w-5xl">
          <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="font-serif text-xl font-semibold tracking-tight text-fg">{tr("labTitle")}</h1>
              <p className="mt-1 max-w-xl text-xs leading-5 text-muted">{tr("labIntro")}</p>
            </div>
            <Link href="/tools" className="flex min-h-[36px] items-center gap-1.5 rounded-[8px] px-2 text-xs text-muted transition-colors hover:bg-surface-hover hover:text-fg">
              <BackMark className="h-4 w-4" />{tr("backToTools")}
            </Link>
          </header>
          {listError && <p role="alert" className="mb-3 text-xs text-danger">{tr("loadFailed")}</p>}
          <ExperimentPicker experiments={experiments} language={language} tr={tr}
            onPick={(id) => router.push(`/tools/lab/chemistry?experiment=${encodeURIComponent(id)}`)} />
          {sessions.length > 0 && (
            <section className="mt-8" aria-label={tr("sessions")}>
              <h2 className="mb-2 text-xs font-semibold text-fg">{tr("sessions")}</h2>
              <ul className="space-y-1.5">
                {sessions.map((row) => (
                  <li key={row.session_id}
                    className="flex items-center gap-2 rounded-[10px] border border-border-light bg-surface px-3 py-2">
                    <button type="button"
                      onClick={() => router.push(`/tools/lab/chemistry?session=${encodeURIComponent(row.session_id)}`)}
                      data-testid="chem-lab-session"
                      className="min-h-[36px] min-w-0 flex-1 cursor-pointer text-left focus-visible:outline-2 focus-visible:outline-accent">
                      <span className="block truncate text-xs font-medium text-fg">
                        {l10n(row.title, language) || tr("unnamed")}
                      </span>
                      <span className="mt-0.5 block text-[10px] text-muted">
                        {tr(`mode.${row.mode}`)} · {tr("revisionLabel").replace("%n", String(row.revision))}
                        {row.finished ? ` · ${tr("phaseCompleted")}` : ""}
                      </span>
                    </button>
                    <Button size="sm" variant="ghost" tone="danger" onClick={() => setDeleting(row)}
                      aria-label={`${tr("delete")}：${l10n(row.title, language) || row.session_id}`}>
                      {tr("delete")}
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
        {deleting && (
          <ConfirmModal open onClose={() => setDeleting(null)} onConfirm={deleteSession}
            title={tr("deleteTitle")} desc={tr("deleteDesc")} confirmText={tr("delete")} cancelText={tr("cancel")} />
        )}
      </div>
    );
  }

  // ---- live bench ----------------------------------------------------------------------
  const equipmentTray = (
    <EquipmentTray pack={pack} display={effectiveDisplay} language={language} selected={selected} busy={busy || commandsLocked}
      onSelect={setSelected}
      onOperate={(next) => setDraft(next)}
      strings={{
        title: tr("trayTitle"), empty: tr("trayEmpty"), clean: tr("trayClean"), dirty: tr("trayDirty"),
        loaded: tr("trayLoaded"), connected: tr("trayConnected"), wash: tr("trayWash"), measure: tr("trayMeasure"),
      }} />
  );
  const reagentPalette = (
    <ReagentPalette pack={pack} display={effectiveDisplay} language={language} selected={selected} busy={busy || commandsLocked}
      onSelect={setSelected}
      onOperate={(next) => setDraft(next)}
      strings={{
        title: tr("paletteTitle"), search: tr("paletteSearch"), empty: tr("paletteEmpty"),
        noMatch: tr("paletteNoMatch"), remaining: tr("remaining"), measureOut: tr("measureOut"), pour: tr("pour"),
      }} />
  );
  const guidanceRail = (
    <GuidanceRail goals={historyActive && historyView ? (historyView.goals ?? []) : snapshot?.goals ?? []}
      goalTitles={goalTitles} guidance={effectiveDisplay.guidance}
      concepts={concepts} modelScopeText={modelScopeText} rejected={Boolean(lastRejection)} tr={tr} />
  );
  const observationLog = (
    <ObservationLog observations={effectiveDisplay.observations} events={effectiveDisplay.events} vesselName={vesselName}
      tr={tr} onOpenEvidence={setEvidenceObs} />
  );

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden bg-bg" data-testid="chem-lab-workspace">
      {banner}
      {historyActive && atNumber !== null && (
        <div role="status" data-testid="chem-lab-readonly-banner"
          className="flex flex-wrap items-center gap-2 border-b border-accent/25 bg-accent-soft/50 px-4 py-2">
          <span className="text-[11px] font-medium text-accent-strong"
            data-testid="chem-lab-history-revision">
            {tr("atBanner").replace("%n", String(atNumber))}
          </span>
          {historyView && (
            <span className="text-[11px] text-fg-secondary">
              {tr("historyTip").replace("%n", String(historyView.tip_revision))}
            </span>
          )}
          <div className="ml-auto flex flex-wrap items-center gap-1.5">
            <Button size="sm" variant="ghost" aria-label={tr("historyPrev")}
              disabled={atNumber <= 0 || acting} onClick={() => jumpRevision(atNumber - 1)}
              data-testid="chem-lab-history-prev">‹</Button>
            <Button size="sm" variant="ghost" aria-label={tr("historyNext")}
              disabled={!historyView || atNumber >= historyView.tip_revision || acting}
              onClick={() => jumpRevision(atNumber + 1)}
              data-testid="chem-lab-history-next">›</Button>
            <Button size="sm" variant="outline" disabled={acting}
              onClick={() => forkSession({ at_revision: atNumber })}>
              {tr("atBranch").replace("%n", String(atNumber))}
            </Button>
            <Button size="sm" variant="outline" onClick={backToLatest}
              data-testid="chem-lab-back-to-latest">{tr("backToLatest")}</Button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="border-b border-danger/30 bg-danger/8 px-4 py-1.5 text-[11px] text-danger">{error}</p>
      )}
      <div className="grid min-h-0 min-w-0 flex-1 grid-cols-1 lg:grid-cols-[248px_minmax(0,1fr)_320px]">
        <aside className="order-3 hidden min-h-0 flex-col overflow-y-auto border-t border-border-light bg-surface px-4 py-4 lg:order-1 lg:flex lg:border-r lg:border-t-0"
          aria-label={tr("equipmentTab")}>
          <Link href="/tools/lab" data-testid="chem-lab-back"
            className="mb-3 flex min-h-[36px] items-center gap-1.5 rounded-[8px] px-2 text-xs text-muted transition-colors hover:bg-surface-hover hover:text-fg">
            <BackMark className="h-4 w-4" />{tr("backToCatalog")}
          </Link>
          <div className="space-y-5">
            {reagentPalette}
            {equipmentTray}
          </div>
        </aside>

        <main className="order-1 flex min-h-0 min-w-0 flex-col lg:order-2" aria-label={tr("labTitle")}>
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-light bg-surface px-4 py-2">
            <div className="min-w-0">
              <p className="truncate text-xs font-semibold text-fg">
                {snapshot ? l10n(pack?.title, language) || snapshot.experiment_id : ""}
              </p>
              <p className="text-[10px] text-muted">
                {snapshot ? `${tr(`mode.${snapshot.mode}`)} · ${tr("revisionLabel").replace("%n", String(display.revision))}` : ""}
              </p>
            </div>
            <Button size="sm" variant="ghost" tone="danger" disabled={busy || !snapshot}
              onClick={() => snapshot && setDeleting({
                session_id: snapshot.session_id, experiment_id: snapshot.experiment_id,
                pack_version: snapshot.pack_version, pack_hash: snapshot.pack_hash, mode: snapshot.mode,
                phase: snapshot.phase, revision: snapshot.revision, finished: snapshot.finished,
                title: (pack?.title ?? {}) as Record<string, string>,
                created_at: snapshot.created_at, updated_at: snapshot.updated_at,
              })}
              data-testid="chem-lab-delete-current">
              {tr("delete")}
            </Button>
          </div>

          <div className="min-h-0 flex-1 overflow-hidden px-2 py-2 sm:px-4">
            {loading && !snapshot ? (
              <p role="status" className="flex h-full items-center justify-center text-sm text-muted">
                <BusyMark className="mr-2 h-4 w-4 animate-spin" />{tr("starting")}
              </p>
            ) : historyActive ? (
              historyError ? (
                <div role="alert"
                  className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
                  <p className="max-w-md text-sm text-danger">{historyError}</p>
                  <Button size="sm" variant="outline" onClick={backToLatest}>{tr("backToLatest")}</Button>
                </div>
              ) : historyDisplay ? (
                <LabStage pack={pack} display={historyDisplay} language={language} selected={selected}
                  busy readOnly sessionId={snapshot?.session_id ?? null}
                  onSelect={setSelected}
                  onDeselect={() => setSelected(null)}
                  onMoveObject={moveObject}
                  onDragOperation={(next) => setDraft(next)}
                  onInstrumentTap={(id, el) => { measureAnchor.current = el; setMeasureId(id); }} />
              ) : (
                <p role="status" className="flex h-full items-center justify-center text-sm text-muted">
                  <BusyMark className="mr-2 h-4 w-4 animate-spin" />{tr("historyLoading")}
                </p>
              )
            ) : (
              <LabStage pack={pack} display={display} language={language} selected={selected}
                busy={busy || commandsLocked}
                sessionId={snapshot?.session_id ?? null}
                motion={motion}
                onSelect={setSelected}
                onDeselect={() => setSelected(null)}
                onMoveObject={moveObject}
                onDragOperation={(next) => setDraft(next)}
                onInstrumentTap={(id, el) => { measureAnchor.current = el; setMeasureId(id); }} />
            )}
          </div>

          <div className="shrink-0 space-y-2 border-t border-border-light bg-surface px-3 py-2.5 sm:px-4">
            <OperationToolbar pack={pack} display={effectiveDisplay} language={language} selected={selected}
              draft={draft} heldId={heldId} busy={busy || commandsLocked} tr={tr}
              onDraft={setDraft} onSubmit={submitCommand} onReleaseHeld={releaseHeld}
              onClearSelection={() => setSelected(null)} />
            <LabTimeline simTimeMs={effectiveDisplay.simTimeMs} revision={effectiveDisplay.revision}
              serverRevision={effectiveDisplay.serverRevision} syncStatus={syncStatus} pendingCount={pendingCount}
              checkpoints={snapshot?.checkpoints ?? []} events={effectiveDisplay.events}
              finished={Boolean(snapshot?.finished)} phase={effectiveDisplay.phase} busy={busy || commandsLocked} tr={tr}
              onCheckpoint={makeCheckpoint}
              onFork={(checkpointId) => forkSession({ checkpoint_id: checkpointId })}
              onReset={() => setResetOpen(true)}
              onFinish={finishSession}
              onCompare={openCompare}
              onJumpRevision={jumpRevision} />
          </div>
        </main>

        <aside className="order-2 hidden min-h-0 flex-col overflow-y-auto border-t border-border-light bg-surface px-4 py-4 lg:order-3 lg:flex lg:border-l lg:border-t-0"
          aria-label={tr("guidanceTab")}>
          <Tabs
            items={[
              { key: "guidance", label: tr("guidanceTab") },
              { key: "observations", label: tr("observationsTab") },
            ]}
            active={railTab}
            onChange={setRailTab}
            className="mb-3"
          />
          {railTab === "guidance" ? guidanceRail : observationLog}
        </aside>
      </div>

      {/* mobile panel bar */}
      <nav className="flex shrink-0 items-stretch gap-1 border-t border-border-light bg-surface px-2 py-1.5 lg:hidden"
        aria-label={tr("equipmentTab")}>
        {(["equipment", "guidance", "observations"] as const).map((panel) => (
          <Button key={panel} size="sm" variant="ghost" selected={mobilePanel === panel}
            className="flex-1" onClick={() => setMobilePanel(panel)}
            data-testid={`chem-lab-mobile-${panel}`}>
            {tr(`${panel}Tab`)}
          </Button>
        ))}
      </nav>

      <Drawer open={mobilePanel !== ""} onClose={() => setMobilePanel("")}
        title={mobilePanel ? tr(`${mobilePanel}Tab`) : ""} width={360}>
        {mobilePanel === "equipment" && <div className="space-y-5">{reagentPalette}{equipmentTray}</div>}
        {mobilePanel === "guidance" && guidanceRail}
        {mobilePanel === "observations" && observationLog}
      </Drawer>

      <MeasurementPopover pack={pack} display={effectiveDisplay} language={language} equipmentId={measureId}
        anchorRef={measureAnchor} open={measureId !== null} busy={busy} tr={tr}
        onClose={() => setMeasureId(null)} onOperate={(next) => setDraft(next)} />

      <EvidenceDrawer pack={pack} engineState={display.engineState as AnyRecord | null}
        events={display.events} observation={evidenceObs} language={language}
        open={evidenceObs !== null} onClose={() => setEvidenceObs(null)} tr={tr} />

      <BranchCompare pack={pack} base={compareSides?.base ?? null} branch={compareSides?.branch ?? null}
        language={language} open={compareOpen} onClose={() => setCompareOpen(false)} tr={tr} />

      <Modal open={resultCard !== null} onClose={() => setResultCard(null)} title={tr("resultTitle")} width={520}>
        {resultCard && (
          <div className="space-y-4" data-testid="chem-lab-result-card">
            <section>
              <h3 className="mb-1.5 text-[11px] font-medium tracking-wide text-muted">{tr("resultGoals")}</h3>
              <ul className="space-y-1">
                {(resultCard.goals ?? []).map((goal) => (
                  <li key={goal.id} className="flex items-center gap-2 text-xs">
                    <span aria-hidden className={`h-2 w-2 rounded-full ${goal.status === "met" ? "bg-accent" : "bg-border"}`} />
                    {goalTitles.get(goal.id) ?? goal.id}
                  </li>
                ))}
              </ul>
            </section>
            <section>
              <h3 className="mb-1.5 text-[11px] font-medium tracking-wide text-muted">{tr("resultObservations")}</h3>
              <ul className="space-y-1 text-xs text-fg-secondary">
                {(resultCard.observations ?? []).map((obs, index) => (
                  <li key={`${obs.seq}-${index}`}>· {tr(`obs.${obs.key}`, obs.key)}{obs.vessel_id ? ` · ${vesselName(obs.vessel_id)}` : ""}</li>
                ))}
              </ul>
            </section>
            {Object.keys(answers).length > 0 && (
              <section>
                <h3 className="mb-1.5 text-[11px] font-medium tracking-wide text-muted">{tr("resultPredictions")}</h3>
                <ul className="space-y-1 text-xs text-fg-secondary">
                  {Object.entries(answers).map(([id, option]) => (
                    <li key={id}>· {id} → {option}</li>
                  ))}
                </ul>
              </section>
            )}
            <p className="text-[10px] leading-4 text-muted">{tr("resultNote")}</p>
          </div>
        )}
      </Modal>

      {resetOpen && (
        <ConfirmModal open onClose={() => setResetOpen(false)} onConfirm={resetSession}
          title={tr("resetTitle")} desc={tr("resetDesc")} confirmText={tr("resetConfirm")} cancelText={tr("cancel")} />
      )}
      {deleting && (
        <ConfirmModal open onClose={() => setDeleting(null)} onConfirm={deleteSession}
          title={tr("deleteTitle")} desc={tr("deleteDesc")} confirmText={tr("delete")} cancelText={tr("cancel")} />
      )}
    </div>
  );
}
