"use client";

/**
 * 工作台组合根（plan §2/§7.1）：stage / document / selection / dirty / undo 的
 * 唯一装配点。文档是权威（domain applyLabAction），显示层经 SceneSync 同步、
 * InteractionController 手势预览在松手时一次性提交。AUTO 一键原子载入
 * assembledTemplate（空瓶/熄灭/泵停）；dirty 时先确认。纯前端娱乐玩具：
 * 无评分、无目标、无指导、零网络请求。
 *
 * 交互类实例只随场景创建一次，最新 React 态经 ref 桥接（写入 effect 声明在
 * 消费 effect 之前，math-workbench Stage3D 同款惯用法）；气泡动作在 render
 * 期只构建纯数据描述，点击时才读取 ref 派发领域动作。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useUIStore } from "@/lib/store";
import { useAuthStore } from "@/lib/auth-store";
import { makePageT } from "@/lib/i18n-page";
import {
  applyAuto, applyLabAction, createHistory, createStageDocument, equipmentLabel,
  getEquipmentSpec, getStage, pushHistory, undoHistory,
  type CameraPose, type ControlSpec, type EquipmentInstance, type LabAction,
  type LabDocument, type StageDefinition,
} from "@next-tutor/domain";
import { Button } from "@/components/ui/Button";
import { ConfirmModal, Modal } from "@/components/ui/Modal";
import { STRINGS } from "./strings.ts";
import { SceneViewport, type SceneBundle } from "./SceneViewport.tsx";
import { SceneSync } from "./scene/SceneSync.ts";
import { InteractionController, type LabSelection } from "./interaction.ts";
import { ObjectPopover, type PopoverAction } from "./ObjectPopover.tsx";
import { WorkbenchHUD } from "./WorkbenchHUD.tsx";
import {
  ChemLabStorageError, deleteChemLabSave, listChemLabSaves, loadChemLabSave,
  saveChemLabDocument, type ChemLabSaveMeta,
} from "./storage.ts";
import "./chem-lab-3d.css";

const POUR_STEP = 0.28;
const LIQUID_DOTS: Record<string, string> = {
  teal: "#2fa08f", sky: "#4a90c2", amber: "#c2843a", violet: "#7d5fc2", magenta: "#b84a7e", pale: "#b9c4c0",
};

type ConfirmKind = "auto" | "reset" | "exit";

/** 气泡动作的纯数据描述：render 期构建，点击时经 ref 桥派发。 */
interface PopoverCommand {
  id: string;
  label: string;
  danger?: boolean;
  dotColor?: string;
  action?: LabAction;
  closeAfter?: boolean;
}

type Quat4 = [number, number, number, number];

function quatFromYaw(angle: number): Quat4 {
  return [0, Math.sin(angle / 2), 0, Math.cos(angle / 2)];
}

function multiplyQuat(a: Quat4, b: Quat4): Quat4 {
  const [ax, ay, az, aw] = a;
  const [bx, by, bz, bw] = b;
  return [
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
    aw * bw - ax * bx - ay * by - az * bz,
  ];
}

export function LabWorkbench(props: { stageId: string }) {
  const lang = useUIStore(s => s.lang);
  const dark = useUIStore(s => s.theme === "dark");
  const owner = useAuthStore(s => s.user?.id ?? "guest");
  const router = useRouter();
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);
  const stage = getStage(props.stageId);

  const [bundle, setBundle] = useState<SceneBundle | null>(null);
  const [doc, setDoc] = useState<LabDocument | null>(null);
  const [selection, setSelection] = useState<LabSelection | null>(null);
  const [dirty, setDirty] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [confirmKind, setConfirmKind] = useState<ConfirmKind | null>(null);
  const [savesOpen, setSavesOpen] = useState(false);
  const [saves, setSaves] = useState<ChemLabSaveMeta[]>([]);
  const [pendingLoadId, setPendingLoadId] = useState<string | null>(null);

  const syncRef = useRef<SceneSync | null>(null);
  const interactionRef = useRef<InteractionController | null>(null);
  const historyRef = useRef(createHistory());
  const docRef = useRef<LabDocument | null>(null);
  const stageRef = useRef<StageDefinition | null>(stage);
  const cameraRef = useRef<CameraPose | null>(null);
  const saveSlotRef = useRef<string | undefined>(undefined);
  const toastTimer = useRef(0);
  const selectionRef = useRef<LabSelection | null>(selection);
  const dispatchRef = useRef<(action: LabAction) => void>(() => {});
  const hintRef = useRef<(key: string) => void>(() => {});
  const cancelGestureRef = useRef(() => {});

  // ref 桥写入（声明在消费 effect 之前：Stage3D 惯用法）。
  useEffect(() => { stageRef.current = stage; }, [stage]);
  useEffect(() => { docRef.current = doc; }, [doc]);
  useEffect(() => { selectionRef.current = selection; }, [selection]);

  const showToast = useCallback((key: string) => {
    setToast(key);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 2600);
  }, []);
  useEffect(() => { hintRef.current = (key) => showToast(tr(key)); }, [showToast, tr]);

  // ── 领域提交：一次动作 → 新文档（失败保留旧场景并刷新显示层） ───────────
  const commit = useCallback((action: LabAction) => {
    const current = docRef.current;
    const currentStage = stageRef.current;
    if (!current || !currentStage) return;
    const result = applyLabAction(current, action, currentStage);
    if (!result.ok) {
      syncRef.current?.refreshWorld(current);
      if (result.hint) showToast(hintText(action, result.hint, tr));
      return;
    }
    historyRef.current = pushHistory(historyRef.current, current);
    docRef.current = result.document;
    setDoc(result.document);
    setDirty(true);
    syncRef.current?.syncDocument(result.document);
    interactionRef.current?.updateSelectionVisual();
    if (action.type === "remove" && selectionRef.current?.id === action.id) setSelection(null);
  }, [showToast, tr]);
  useEffect(() => { dispatchRef.current = commit; }, [commit]);
  useEffect(() => { cancelGestureRef.current = () => interactionRef.current?.cancelAll(); }, []);

  // ── 场景装配：sync + interaction + 初始起步场景（非 AUTO） ───────────────
  useEffect(() => {
    if (!bundle || !stage) return;
    const sync = new SceneSync(bundle.controller.tier);
    syncRef.current = sync;
    bundle.controller.root.add(sync.root, sync.tubes.group);
    const initial = createStageDocument(stage);
    docRef.current = initial;
    historyRef.current = createHistory();
    sync.syncDocument(initial);
    cameraRef.current = stage.camera;
    bundle.controller.applyCameraPose(stage.camera);
    const interaction = new InteractionController(bundle.controller, sync, {
      getDocument: () => docRef.current ?? initial,
      getSelection: () => selectionRef.current,
      setSelection: (next) => setSelection(next),
      commit: (action) => dispatchRef.current(action),
      hint: (key) => hintRef.current(key),
      onDragStateChange: (active) => setDragging(active),
    });
    interactionRef.current = interaction;
    bundle.controller.invalidate();
    // e2e 探针（只读 + 派发）：SceneViewport 先建 __chemLabDebug，这里补文档侧。
    const debugWindow = window as unknown as { __chemLabDebug?: Record<string, unknown> };
    debugWindow.__chemLabDebug = {
      ...(debugWindow.__chemLabDebug ?? {}),
      sync,
      interaction,
      doc: () => docRef.current,
      commit: (action: LabAction) => dispatchRef.current(action),
    };
    // 初始 state 经延时落地（repo lint 合规模式），场景内容同步保持即时。
    const readyTimer = window.setTimeout(() => {
      setDoc(initial);
      setDirty(false);
      setSelection(null);
    }, 0);
    return () => {
      window.clearTimeout(readyTimer);
      const debug = (window as unknown as { __chemLabDebug?: Record<string, unknown> }).__chemLabDebug;
      if (debug) { delete debug.sync; delete debug.interaction; delete debug.doc; delete debug.commit; }
      interaction.dispose();
      interactionRef.current = null;
      sync.dispose();
      syncRef.current = null;
    };
  }, [bundle, stage]);

  // ── AUTO：一次性原子载入预组装模板（plan §4.5） ─────────────────────────
  const runAuto = useCallback(() => {
    const current = docRef.current;
    const currentStage = stageRef.current;
    if (!current || !currentStage || !bundle) return;
    try {
      const next = applyAuto(current, currentStage);
      historyRef.current = pushHistory(historyRef.current, current);
      docRef.current = next;
      setDoc(next);
      setSelection(null);
      syncRef.current?.syncDocument(next);
      const camera = currentStage.assembledTemplate.camera ?? currentStage.camera;
      cameraRef.current = camera;
      bundle.controller.applyCameraPose(camera);
      bundle.controller.invalidate();
      // AUTO 结果是新的基线（再次 AUTO 无编辑则直接重载，不重复叠加）。
      setDirty(false);
      saveSlotRef.current = undefined;
    } catch {
      showToast(tr("chem3d.hint.autoFailed"));
    }
  }, [bundle, showToast, tr]);

  const onAuto = useCallback(() => {
    if (dirty) setConfirmKind("auto");
    else runAuto();
  }, [dirty, runAuto]);

  const runReset = useCallback(() => {
    const current = docRef.current;
    const currentStage = stageRef.current;
    if (!current || !currentStage || !bundle) return;
    const result = applyLabAction(current, { type: "reset" }, currentStage);
    if (!result.ok) return;
    historyRef.current = pushHistory(historyRef.current, current);
    docRef.current = result.document;
    setDoc(result.document);
    setSelection(null);
    syncRef.current?.syncDocument(result.document);
    cameraRef.current = currentStage.camera;
    bundle.controller.applyCameraPose(currentStage.camera);
    bundle.controller.invalidate();
    setDirty(false);
    saveSlotRef.current = undefined;
  }, [bundle]);

  // ── 存档：保存/打开/删除（owner 分区；失败明确告知） ─────────────────────
  const quickSave = useCallback(() => {
    const current = docRef.current;
    const currentStage = stageRef.current;
    if (!current || !currentStage) return;
    const now = new Date();
    const stamp = `${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")} ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
    const name = `${currentStage.title[lang]} · ${stamp}`;
    try {
      const meta = saveChemLabDocument(owner, current, name, saveSlotRef.current);
      saveSlotRef.current = meta.id;
      setDirty(false);
      showToast(tr("chem3d.toast.saved"));
    } catch (error) {
      showToast(storageErrorText(error, tr));
    }
  }, [lang, owner, showToast, tr]);

  const doLoad = useCallback((saveId: string) => {
    try {
      const loaded = loadChemLabSave(owner, saveId);
      if (loaded.stageId !== props.stageId) {
        // 跨关存档：切到存档所属关卡（关卡本身仍在目录内）。
        router.replace(`/tools/lab/chemistry?stage=${encodeURIComponent(loaded.stageId)}`);
        return;
      }
      const current = docRef.current;
      if (current) historyRef.current = pushHistory(historyRef.current, current);
      docRef.current = loaded;
      setDoc(loaded);
      setSelection(null);
      syncRef.current?.syncDocument(loaded);
      bundle?.controller.invalidate();
      setDirty(false);
      saveSlotRef.current = saveId;
      setSavesOpen(false);
      showToast(tr("chem3d.toast.opened"));
    } catch (error) {
      showToast(storageErrorText(error, tr));
    }
  }, [bundle, owner, props.stageId, router, showToast, tr]);

  const openSaves = useCallback(() => {
    setSaves(listChemLabSaves(owner));
    setSavesOpen(true);
  }, [owner]);

  // ── 快捷键：Delete/Esc/Ctrl+Z/Ctrl+S（plan §4.6） ───────────────────────
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!doc) return;
      const mod = event.ctrlKey || event.metaKey;
      if (mod && event.key.toLowerCase() === "z") {
        event.preventDefault();
        const current = docRef.current;
        if (!current) return;
        const undo = undoHistory(historyRef.current, current);
        if (undo.history === historyRef.current) return;
        historyRef.current = undo.history;
        docRef.current = undo.document;
        setDoc(undo.document);
        setDirty(true);
        syncRef.current?.syncDocument(undo.document);
        interactionRef.current?.updateSelectionVisual();
        return;
      }
      if (mod && event.key.toLowerCase() === "s") {
        event.preventDefault();
        quickSave();
        return;
      }
      if (event.key === "Escape") {
        // 手势层先消费（捕获阶段）；这里只负责收起气泡/存档面板。
        setSavesOpen(false);
        setSelection(null);
        interactionRef.current?.updateSelectionVisual();
        return;
      }
      if ((event.key === "Delete" || event.key === "Backspace") && selectionRef.current?.type === "equipment") {
        const target = document.activeElement;
        if (target instanceof HTMLElement && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return;
        event.preventDefault();
        const id = selectionRef.current.id;
        dispatchRef.current({ type: "remove", id });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [doc, quickSave]);

  // ── 首次到访提示（3–5s 自动消失，无欢迎模态） ───────────────────────────
  useEffect(() => {
    if (!bundle) return;
    let seen = false;
    try {
      seen = Boolean(localStorage.getItem("chem-lab.hint-v1"));
      if (!seen) localStorage.setItem("chem-lab.hint-v1", "1");
    } catch { /* 私有模式忽略 */ }
    if (seen) return;
    const hintTimer = window.setTimeout(() => showToast(tr("chem3d.hint.firstVisit")), 0);
    return () => window.clearTimeout(hintTimer);
  }, [bundle, showToast, tr]);

  if (!stage) {
    return (
      <div className="chem-scene-fallback">
        <div className="chem-scene-fallback-card">
          <p className="chem-scene-fallback-title">{tr("chem3d.stageMissing")}</p>
          <p className="chem-scene-fallback-hint">{tr("chem3d.stageMissingHint")}</p>
          <Link className="chem-scene-fallback-link" href="/tools/lab">{tr("chem3d.backToCatalog")}</Link>
        </div>
      </div>
    );
  }

  // ── 气泡命令（纯数据；render 期不触碰 ref/回调派发） ────────────────────
  const selectedEq = selection?.type === "equipment" && doc ? doc.equipment.find(e => e.id === selection.id) ?? null : null;
  const selectedConn = selection?.type === "connection" && doc ? doc.connections.find(c => c.id === selection.id) ?? null : null;
  const commands = buildPopoverCommands(doc, stage, lang, selectedEq, selectedConn, tr);
  const popoverActions: PopoverAction[] = commands.map((command) => ({
    id: command.id,
    label: command.label,
    danger: command.danger,
    dotColor: command.dotColor,
    onClick: () => {
      if (command.action) dispatchRef.current(command.action);
      if (command.closeAfter) setSelection(null);
    },
  }));

  const goBack = () => {
    if (dirty) setConfirmKind("exit");
    else router.push("/tools/lab");
  };

  return (
    <div className="chem-workbench">
      <SceneViewport
        dark={dark}
        onScene={setBundle}
        onContextLost={() => cancelGestureRef.current()}
        strings={{
          webglFailed: tr("chem3d.webglFailed"),
          webglFailedHint: tr("chem3d.webglFailedHint"),
          backToCatalog: tr("chem3d.backToCatalog"),
          contextLost: tr("chem3d.contextLost"),
          loading: tr("chem3d.loading"),
        }}
      >
        <WorkbenchHUD
          backHref="/tools/lab"
          stageTitle={stage.title[lang]}
          dirty={dirty}
          onBack={goBack}
          strings={{
            back: tr("chem3d.back"),
            auto: tr("chem3d.auto"),
            save: tr("chem3d.save"),
            open: tr("chem3d.open"),
            reset: tr("chem3d.reset"),
            resetCamera: tr("chem3d.resetCamera"),
            more: tr("chem3d.more"),
          }}
          onAuto={onAuto}
          onSave={quickSave}
          onOpen={openSaves}
          onReset={() => setConfirmKind("reset")}
          onResetCamera={() => {
            if (cameraRef.current) bundle?.controller.resetCamera(cameraRef.current);
          }}
        />
        {!dragging && (selectedEq !== null || selectedConn !== null) && bundle ? (
          <ObjectPopover
            controller={bundle.controller}
            getAnchorWorld={() => (selection ? syncRef.current?.selectionAnchor(selection) ?? null : null)}
            title={selectedEq ? equipmentLabel(selectedEq.kind, lang) : tr("chem3d.tube")}
            actions={popoverActions}
            onClose={() => setSelection(null)}
          />
        ) : null}
        {toast && <div className="chem-toast" role="status">{toast}</div>}
      </SceneViewport>

      <ConfirmModal
        open={confirmKind === "auto"}
        onClose={() => setConfirmKind(null)}
        onConfirm={() => { setConfirmKind(null); runAuto(); }}
        title={tr("chem3d.autoConfirmTitle")}
        desc={tr("chem3d.autoConfirmDesc")}
        confirmText={tr("chem3d.autoConfirmYes")}
        cancelText={tr("chem3d.cancel")}
      />
      <ConfirmModal
        open={confirmKind === "reset"}
        onClose={() => setConfirmKind(null)}
        onConfirm={() => { setConfirmKind(null); runReset(); }}
        title={tr("chem3d.resetConfirmTitle")}
        desc={tr("chem3d.resetConfirmDesc")}
        confirmText={tr("chem3d.resetConfirmYes")}
        cancelText={tr("chem3d.cancel")}
      />
      <ConfirmModal
        open={confirmKind === "exit"}
        onClose={() => setConfirmKind(null)}
        onConfirm={() => { setConfirmKind(null); router.push("/tools/lab"); }}
        title={tr("chem3d.exitConfirmTitle")}
        desc={tr("chem3d.exitConfirmDesc")}
        confirmText={tr("chem3d.exitConfirmYes")}
        cancelText={tr("chem3d.cancel")}
      />
      <Modal
        open={savesOpen}
        onClose={() => setSavesOpen(false)}
        title={tr("chem3d.savesTitle")}
        width={460}
        testId="chem-lab-saves"
      >
        <div className="chem-saves-list">
          {saves.length === 0 ? (
            <p className="chem-saves-empty">{tr("chem3d.savesEmpty")}</p>
          ) : saves.map((save) => (
            <div key={save.id} className="chem-saves-row" data-testid="chem-lab-save-row">
              <div className="chem-saves-info">
                <p className="chem-saves-name">{save.name}</p>
                <p className="chem-saves-meta">{stageTitleOf(save.stageId, lang)} · {formatTime(save.savedAt)}</p>
              </div>
              <div className="chem-saves-ops">
                <Button variant="ghost" size="sm" onClick={() => {
                  if (dirty) { setPendingLoadId(save.id); setSavesOpen(false); }
                  else doLoad(save.id);
                }}>{tr("chem3d.open")}</Button>
                <Button variant="ghost" size="sm" onClick={() => {
                  deleteChemLabSave(owner, save.id);
                  if (saveSlotRef.current === save.id) saveSlotRef.current = undefined;
                  setSaves(listChemLabSaves(owner));
                }}>{tr("chem3d.delete")}</Button>
              </div>
            </div>
          ))}
        </div>
      </Modal>
      <ConfirmModal
        open={pendingLoadId !== null}
        onClose={() => setPendingLoadId(null)}
        onConfirm={() => { const id = pendingLoadId; setPendingLoadId(null); if (id) doLoad(id); }}
        title={tr("chem3d.loadConfirmTitle")}
        desc={tr("chem3d.loadConfirmDesc")}
        confirmText={tr("chem3d.loadConfirmYes")}
        cancelText={tr("chem3d.cancel")}
      />
    </div>
  );
}

// ── 纯辅助（render 安全：只读入参，不触碰 ref/回调） ────────────────────────

function stageTitleOf(stageId: string, lang: "zh" | "en"): string {
  return getStage(stageId)?.title[lang] ?? stageId;
}

function formatTime(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function hintText(action: LabAction, hint: string, tr: (k: string) => string): string {
  if (action.type === "connect" || hint.startsWith("port_")) return tr("chem3d.hint.portBusy");
  if (action.type === "add" || hint === "limit_reached") return tr("chem3d.hint.limitReached");
  if (action.type === "attach") return tr("chem3d.hint.mountNotAllowed");
  return tr("chem3d.hint.portNotConnectable");
}

function storageErrorText(error: unknown, tr: (k: string) => string): string {
  if (error instanceof ChemLabStorageError) {
    if (error.code === "too_many_saves") return tr("chem3d.toast.tooSaves");
    if (error.code === "too_large" || error.code === "serialize_failed") return tr("chem3d.toast.tooLarge");
    if (error.code === "quota") return tr("chem3d.toast.quota");
    if (error.code === "not_found") return tr("chem3d.toast.notFound");
    if (error.code === "bad_save") return tr("chem3d.toast.badSave");
  }
  return tr("chem3d.toast.quota");
}

/** 容器判定：有 liquidSurface 锚点即视为可加演示液。 */
function isLiquidContainer(eq: EquipmentInstance): boolean {
  return Boolean(getEquipmentSpec(eq.kind)?.anchors.liquidSurface);
}

function controlToggleLabel(control: ControlSpec, eq: EquipmentInstance, tr: (k: string) => string): string {
  const on = Boolean(eq.controls[control.id]);
  if (control.id === "lit") return on ? tr("chem3d.act.extinguish") : tr("chem3d.act.ignite");
  if (control.id === "open") return on ? tr("chem3d.act.valveClose") : tr("chem3d.act.valveOpen");
  return on ? tr("chem3d.act.powerOff") : tr("chem3d.act.powerOn");
}

function dialLabel(control: ControlSpec, eq: EquipmentInstance, tr: (k: string) => string): string {
  const steps = control.steps ?? [];
  const index = steps.findIndex(s => s === eq.controls[control.id]);
  return `${tr("chem3d.act.turn")} · ${index >= 0 ? index + 1 : 1}`;
}

function buildPopoverCommands(
  doc: LabDocument | null,
  stage: StageDefinition | null,
  lang: "zh" | "en",
  selectedEq: EquipmentInstance | null,
  selectedConn: { id: string } | null,
  tr: (key: string) => string,
): PopoverCommand[] {
  if (!doc || !stage) return [];
  const commands: PopoverCommand[] = [];

  if (selectedConn) {
    commands.push({
      id: "disconnect",
      label: tr("chem3d.act.disconnect"),
      danger: true,
      action: { type: "disconnect", connectionId: selectedConn.id },
      closeAfter: true,
    });
    return commands;
  }
  if (!selectedEq) return [];
  const spec = getEquipmentSpec(selectedEq.kind);
  if (!spec) return [];

  if (isLiquidContainer(selectedEq)) {
    for (const colorId of stage.cueMap.liquidColors.slice(0, 3)) {
      const fill = Math.min(1, (selectedEq.visualContents?.fill ?? 0) + POUR_STEP);
      commands.push({
        id: `pour-${colorId}`,
        label: tr("chem3d.act.addLiquid"),
        dotColor: LIQUID_DOTS[colorId] ?? LIQUID_DOTS.teal,
        action: { type: "setVisualContents", id: selectedEq.id, contents: { fill, colorId } },
      });
    }
    if ((selectedEq.visualContents?.fill ?? 0) > 0) {
      commands.push({
        id: "empty",
        label: tr("chem3d.act.empty"),
        action: { type: "setVisualContents", id: selectedEq.id, contents: { fill: 0 } },
      });
    }
  }

  for (const control of spec.controls) {
    if (control.kind === "toggle") {
      commands.push({
        id: `control-${control.id}`,
        label: controlToggleLabel(control, selectedEq, tr),
        action: { type: "setControl", id: selectedEq.id, controlId: control.id, value: !selectedEq.controls[control.id] },
      });
    } else {
      const steps = control.steps ?? [1];
      const index = Math.max(0, steps.findIndex(s => s === selectedEq.controls[control.id]));
      const next = steps[(index + 1) % steps.length] ?? steps[0]!;
      commands.push({
        id: `control-${control.id}`,
        label: dialLabel(control, selectedEq, tr),
        action: { type: "setControl", id: selectedEq.id, controlId: control.id, value: next },
      });
    }
  }

  if (spec.family === "glassware" || spec.family === "connector") {
    const current = selectedEq.parentMountId
      ? selectedEq.localPose?.rotation ?? selectedEq.pose.rotation
      : selectedEq.pose.rotation;
    commands.push({
      id: "rotate",
      label: tr("chem3d.act.rotate"),
      action: { type: "rotate", id: selectedEq.id, rotation: multiplyQuat(quatFromYaw(Math.PI / 4), current) },
    });
  }

  if (!selectedEq.parentMountId) {
    const p = selectedEq.pose.position;
    commands.push({
      id: "duplicate",
      label: tr("chem3d.act.duplicate"),
      action: {
        type: "add",
        kind: selectedEq.kind,
        pose: {
          position: { x: Math.min(7.4, p.x + 0.9), y: p.y, z: Math.max(-3.4, p.z - 0.8) },
          rotation: selectedEq.pose.rotation,
        },
      },
    });
  }

  commands.push({
    id: "remove",
    label: tr("chem3d.act.remove"),
    danger: true,
    action: { type: "remove", id: selectedEq.id },
    closeAfter: true,
  });
  return commands;
}
