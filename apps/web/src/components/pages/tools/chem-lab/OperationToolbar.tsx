"use client";

/**
 * Operation panel + the single command factory. Every path (click, keyboard,
 * drag-drop, tray quick action) lands here as an OperationDraft; the panel
 * collects amount/rate/power/duration/quantity, previews the participants and
 * emits exactly one closed LabCommand via buildLabCommand. Dragging a whole
 * bottle in never pours it — the panel always asks first.
 *
 * The parameter form is a keyed child component: when the draft changes the
 * form remounts and re-initialises from the new draft, so no reset effect is
 * needed.
 */
import { useMemo, useState } from "react";
import { Button } from "@/components/ui/Button";
import { FIELD_CLS, Field, Input } from "@/components/ui/Input";
import type { LabCommand } from "@/lib/api-chem-lab";
import type { ChemLabDisplay } from "./useChemLabSession";
import {
  buildLabCommand,
  type ChemLabObjectRef,
  type OperationDraft,
} from "./interaction";

type AnyRecord = Record<string, unknown>;

function l10n(entry: unknown, language: string): string {
  if (entry && typeof entry === "object" && !Array.isArray(entry)) {
    const row = entry as AnyRecord;
    const value = row[language] ?? row.zh ?? row.en;
    if (typeof value === "string") return value;
  }
  return "";
}

interface ObjectOption {
  id: string;
  label: string;
}

export interface OperationToolbarProps {
  pack: AnyRecord | null;
  display: ChemLabDisplay;
  language: string;
  selected: ChemLabObjectRef | null;
  draft: OperationDraft | null;
  heldId: string | null;
  busy: boolean;
  tr: (key: string, fallback?: string) => string;
  onDraft: (draft: OperationDraft | null) => void;
  onSubmit: (command: LabCommand) => void;
  /** Drop a server-side hold via the atomic `release` command. */
  onReleaseHeld: () => void;
  onClearSelection: () => void;
}

const DURATION_PRESETS = [10000, 30000, 60000, 120000];

function amountPresets(instrumentCapacityUL: number | null): number[] {
  if (instrumentCapacityUL !== null && instrumentCapacityUL <= 5000) {
    return [500, 1000, 2000];
  }
  if (instrumentCapacityUL !== null && instrumentCapacityUL <= 15000) {
    return [1000, 5000, 10000];
  }
  return [5000, 10000, 25000, 50000];
}

/** Prefill a sensible starting amount so the panel opens one-confirm ready. */
function initialAmountMl(
  action: OperationDraft["action"],
  lookups: BenchLookups,
  draft: OperationDraft,
): string {
  if (action === "aspirate") {
    const kind = lookups.equipmentKind.get(draft.instrumentId ?? "") ?? "";
    const capacity = Number((lookups.equipmentDefs[kind] as AnyRecord | undefined)?.capacity_uL ?? 0);
    if (capacity > 0) {
      const mid = capacity <= 2000 ? 1000 : capacity <= 12000 ? 5000 : 10000;
      return String(Math.min(capacity, mid) / 1000);
    }
    return "10";
  }
  if (action === "dispense") {
    const load = lookups.equipmentLoad.get(draft.instrumentId ?? "") ?? 0;
    return load > 0 ? String(load / 1000) : "5";
  }
  if (action === "pour" || action === "dispose") return "10";
  return "";
}

interface BenchLookups {
  vesselOptions: ObjectOption[];
  equipmentOptions: ObjectOption[];
  equipmentKind: Map<string, string>;
  vesselKind: Map<string, string>;
  equipmentLoad: Map<string, number>;
  equipmentDefs: AnyRecord;
  transferInstruments: ObjectOption[];
  tubingOptions: ObjectOption[];
  heaterOptions: ObjectOption[];
  measuringInstruments: ObjectOption[];
}

function useBenchLookups(
  pack: AnyRecord | null,
  display: ChemLabDisplay,
  language: string,
): BenchLookups {
  const equipmentDefs = useMemo(
    () => ((pack?._equipment_defs ?? pack?.equipment_defs ?? {}) as AnyRecord),
    [pack],
  );

  return useMemo(() => {
    const start = (pack?.starting_state ?? {}) as AnyRecord;
    const labels = new Map<string, string>();
    for (const vessel of Array.isArray(start.vessels) ? (start.vessels as AnyRecord[]) : []) {
      labels.set(String(vessel.id ?? ""), l10n(vessel.label, language));
    }
    const vessels = (display.engineState?.vessels ?? {}) as AnyRecord;
    const equipment = (display.engineState?.equipment ?? {}) as AnyRecord;
    const vesselKind = new Map<string, string>();
    const equipmentKind = new Map<string, string>();
    const equipmentLoad = new Map<string, number>();
    const vesselOptions: ObjectOption[] = [];
    const equipmentOptions: ObjectOption[] = [];
    for (const id of Object.keys(vessels).sort()) {
      vesselKind.set(id, String((vessels[id] as AnyRecord).kind ?? ""));
      vesselOptions.push({ id, label: labels.get(id) || id });
    }
    for (const id of Object.keys(equipment).sort()) {
      const item = equipment[id] as AnyRecord;
      const kind = String(item.kind ?? "");
      equipmentKind.set(id, kind);
      equipmentLoad.set(id, Number(((item.load ?? {}) as AnyRecord).volume_uL ?? 0));
      equipmentOptions.push({ id, label: l10n((equipmentDefs[kind] as AnyRecord)?.name, language) || id });
    }
    const measures = (id: string, kindMap: Map<string, string>): string[] => {
      const def = equipmentDefs[kindMap.get(id) ?? ""] as AnyRecord | undefined;
      return Array.isArray(def?.measures) ? (def.measures as string[]) : [];
    };
    return {
      vesselOptions,
      equipmentOptions,
      equipmentKind,
      vesselKind,
      equipmentLoad,
      equipmentDefs,
      transferInstruments: equipmentOptions.filter((option) => {
        const kind = equipmentKind.get(option.id) ?? "";
        return kind === "dropper" || kind === "pipette";
      }),
      tubingOptions: equipmentOptions.filter((option) => (equipmentKind.get(option.id) ?? "") === "delivery_tube"),
      heaterOptions: equipmentOptions.filter((option) => (equipmentKind.get(option.id) ?? "") === "hotplate"),
      measuringInstruments: [
        ...equipmentOptions.filter((option) => measures(option.id, equipmentKind).length > 0),
        ...vesselOptions.filter((option) => measures(option.id, vesselKind).length > 0),
      ],
    };
  }, [pack, display.engineState, equipmentDefs, language]);
}

export function OperationToolbar(props: OperationToolbarProps) {
  const {
    pack, display, language, selected, draft, heldId, busy, tr,
    onDraft, onSubmit, onReleaseHeld, onClearSelection,
  } = props;
  const lookups = useBenchLookups(pack, display, language);

  if (heldId) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-[12px] border border-accent/30 bg-accent-soft/60 px-3 py-2"
        role="status" data-testid="chem-lab-held-hint">
        <span className="text-xs text-fg-secondary">{tr("heldHint")}</span>
        <Button size="sm" variant="ghost" onClick={onReleaseHeld}>{tr("releaseHold")}</Button>
      </div>
    );
  }

  if (!draft) {
    if (!selected) {
      return (
        <div className="flex items-center gap-2" data-testid="chem-lab-toolbar-idle">
          <Button size="sm" variant="outline" disabled={busy}
            onClick={() => onDraft({ action: "wait" })} data-testid="chem-lab-wait">
            {tr("wait")}
          </Button>
          <span className="text-[11px] text-muted">{tr("selectHint")}</span>
        </div>
      );
    }
    const isVessel = selected.type === "vessel";
    const kind = isVessel
      ? lookups.vesselKind.get(selected.id) ?? ""
      : lookups.equipmentKind.get(selected.id) ?? "";
    const def = lookups.equipmentDefs[kind] as AnyRecord | undefined;
    const actions: Array<{ key: string; draftValue: OperationDraft; test: string }> = [];
    if (isVessel) {
      actions.push(
        { key: "pour", draftValue: { action: "pour", sourceId: selected.id }, test: "pour" },
        { key: "stir", draftValue: { action: "stir", vesselId: selected.id }, test: "stir" },
        { key: "measure", draftValue: { action: "measure", vesselId: selected.id }, test: "measure" },
        { key: "filter", draftValue: { action: "filter", vesselId: selected.id }, test: "filter" },
        { key: "dispose", draftValue: { action: "dispose", vesselId: selected.id }, test: "dispose" },
      );
      if (def?.heat_compatible) {
        actions.splice(1, 0, { key: "heat", draftValue: { action: "heat", vesselId: selected.id }, test: "heat" });
      }
    } else if (kind === "dropper" || kind === "pipette") {
      actions.push(
        { key: "aspirate", draftValue: { action: "aspirate", instrumentId: selected.id }, test: "aspirate" },
        { key: "dispense", draftValue: { action: "dispense", instrumentId: selected.id }, test: "dispense" },
        { key: "wash", draftValue: { action: "wash", instrumentId: selected.id }, test: "wash" },
      );
    } else if (kind === "delivery_tube") {
      actions.push({ key: "connect", draftValue: { action: "connect", instrumentId: selected.id }, test: "connect" });
    } else if (kind === "hotplate") {
      actions.push({ key: "heat", draftValue: { action: "heat", deviceId: selected.id }, test: "heat" });
    } else {
      actions.push({ key: "measure", draftValue: { action: "measure", instrumentId: selected.id }, test: "measure" });
    }
    return (
      <div className="flex flex-wrap items-center gap-1.5" data-testid="chem-lab-actions">
        {actions.map((action) => (
          <Button key={action.key} size="sm" variant="outline" disabled={busy}
            onClick={() => onDraft(action.draftValue)} data-testid={`chem-lab-action-${action.test}`}>
            {tr(action.key)}
          </Button>
        ))}
        <Button size="sm" variant="outline" disabled={busy}
          onClick={() => onDraft({ action: "wait" })} data-testid="chem-lab-action-wait">
          {tr("wait")}
        </Button>
        <Button size="sm" variant="ghost" onClick={onClearSelection}>{tr("deselect")}</Button>
      </div>
    );
  }

  return (
    <OperationForm
      key={JSON.stringify(draft)}
      draft={draft}
      busy={busy}
      tr={tr}
      lookups={lookups}
      onSubmit={onSubmit}
      onBack={() => onDraft(null)}
    />
  );
}

function OperationForm({
  draft,
  busy,
  tr,
  lookups,
  onSubmit,
  onBack,
}: {
  draft: OperationDraft;
  busy: boolean;
  tr: (key: string, fallback?: string) => string;
  lookups: BenchLookups;
  onSubmit: (command: LabCommand) => void;
  onBack: () => void;
}) {
  const [amountMl, setAmountMl] = useState(() => initialAmountMl(draft.action, lookups, draft));
  const [rate, setRate] = useState("normal");
  const [power, setPower] = useState(500);
  const [speed, setSpeed] = useState(500);
  const [durationMs, setDurationMs] = useState(30000);
  const [quantity, setQuantity] = useState("");
  const [instrumentId, setInstrumentId] = useState(draft.instrumentId ?? "");
  const [vesselId, setVesselId] = useState(draft.vesselId ?? "");
  const [targetId, setTargetId] = useState(draft.targetId ?? "");
  const [deviceId, setDeviceId] = useState(draft.deviceId ?? "");
  const [apparatusId, setApparatusId] = useState(draft.apparatusId ?? "");

  const {
    vesselOptions, equipmentOptions, equipmentKind, vesselKind, equipmentLoad, equipmentDefs,
    transferInstruments, tubingOptions, heaterOptions, measuringInstruments,
  } = lookups;

  const submit = () => {
    const amountUL = amountMl.trim() ? Math.max(1, Math.round(Number(amountMl) * 1000)) : undefined;
    const command = buildLabCommand(draft, {
      amountUL,
      rate,
      powerPermille: power,
      speedPermille: speed,
      durationMs,
      quantity: quantity || undefined,
      instrumentId: instrumentId || undefined,
      vesselId: vesselId || undefined,
      sourceId: draft.sourceId,
      targetId: targetId || undefined,
      deviceId: deviceId || undefined,
      apparatusId: apparatusId || undefined,
    });
    onSubmit(command);
  };

  const selectCls = `${FIELD_CLS} h-8 text-xs`;
  const needInstrument = draft.action === "aspirate" || draft.action === "dispense";
  const instrumentCapacity = needInstrument && instrumentId
    ? Number((equipmentDefs[equipmentKind.get(instrumentId) ?? ""] as AnyRecord)?.capacity_uL ?? 0) || null
    : null;
  const presets = amountPresets(instrumentCapacity);
  const defaultLoad = draft.action === "dispense" && instrumentId ? equipmentLoad.get(instrumentId) ?? 0 : 0;
  const effectiveAmount = amountMl.trim()
    ? Math.max(1, Math.round(Number(amountMl) * 1000))
    : (defaultLoad || null);
  const measureQuantities = (() => {
    if (draft.action !== "measure" || !instrumentId) return [] as string[];
    const kind = vesselKind.has(instrumentId)
      ? vesselKind.get(instrumentId) ?? ""
      : equipmentKind.get(instrumentId) ?? "";
    const def = equipmentDefs[kind] as AnyRecord | undefined;
    return Array.isArray(def?.measures) ? (def.measures as string[]) : [];
  })();
  const quantityOptions = measureQuantities.length ? measureQuantities : ["temperature", "ph", "volume"];
  const canSubmit = (() => {
    if (busy) return false;
    switch (draft.action) {
      case "aspirate":
      case "dispense":
        return Boolean(instrumentId) && effectiveAmount !== null
          && (draft.action === "aspirate" ? Boolean(draft.sourceId ?? vesselId) : Boolean(targetId));
      case "pour":
        return Boolean(draft.sourceId ?? vesselId) && Boolean(targetId) && effectiveAmount !== null;
      case "heat":
        return Boolean(deviceId) && Boolean(vesselId || draft.vesselId);
      case "stir":
        return Boolean(vesselId || draft.vesselId);
      case "wait":
        return durationMs > 0;
      case "measure":
        return Boolean(instrumentId) && Boolean(vesselId || draft.vesselId) && Boolean(quantity || quantityOptions[0]);
      case "connect":
        return Boolean(instrumentId) && Boolean(vesselId || draft.vesselId) && Boolean(targetId);
      case "filter":
        return Boolean(vesselId || draft.vesselId) && Boolean(apparatusId) && (vesselId || draft.vesselId) !== apparatusId;
      case "wash":
        return Boolean(instrumentId || draft.instrumentId);
      case "dispose":
        return Boolean(vesselId || draft.vesselId) && effectiveAmount !== null;
      default:
        return false;
    }
  })();

  const vesselField = (label: string, value: string, set: (v: string) => void, exclude?: string) => (
    <Field label={label} className="min-w-[130px]">
      <select className={selectCls} value={value} onChange={(event) => set(event.target.value)}
        aria-label={label} disabled={busy}>
        <option value="">{tr("choose")}</option>
        {vesselOptions.filter((option) => option.id !== exclude).map((option) => (
          <option key={option.id} value={option.id}>{option.label}</option>
        ))}
      </select>
    </Field>
  );

  return (
    <div className="rounded-[12px] border border-border bg-surface px-3 py-3 shadow-sm" data-testid="chem-lab-operation-panel">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-xs font-semibold text-fg">{tr(draft.action)}{tr("operationSuffix")}</p>
        <Button size="sm" variant="ghost" onClick={onBack}>{tr("back")}</Button>
      </div>
      <div className="flex flex-wrap items-end gap-2.5">
        {needInstrument && (
          <Field label={tr("instrument")} className="min-w-[130px]">
            <select className={selectCls} value={instrumentId} disabled={busy}
              onChange={(event) => setInstrumentId(event.target.value)} aria-label={tr("instrument")}>
              <option value="">{tr("choose")}</option>
              {transferInstruments.map((option) => (
                <option key={option.id} value={option.id}>{option.label}</option>
              ))}
            </select>
          </Field>
        )}
        {draft.action === "aspirate" && !draft.sourceId &&
          vesselField(tr("sourceVessel"), vesselId, setVesselId)}
        {(draft.action === "dispense" || draft.action === "pour" || draft.action === "connect") &&
          vesselField(tr("targetVessel"), targetId, setTargetId, draft.sourceId ?? (draft.action === "connect" ? vesselId || draft.vesselId : undefined))}
        {draft.action === "pour" && !draft.sourceId &&
          vesselField(tr("sourceVessel"), vesselId, setVesselId, targetId)}
        {(draft.action === "heat" || draft.action === "stir" || draft.action === "measure" || draft.action === "connect") && !draft.vesselId &&
          vesselField(tr("vessel"), vesselId, setVesselId)}
        {draft.action === "heat" && (
          <Field label={tr("device")} className="min-w-[130px]">
            <select className={selectCls} value={deviceId} disabled={busy}
              onChange={(event) => setDeviceId(event.target.value)} aria-label={tr("device")}>
              <option value="">{tr("choose")}</option>
              {heaterOptions.map((option) => (
                <option key={option.id} value={option.id}>{option.label}</option>
              ))}
            </select>
          </Field>
        )}
        {draft.action === "measure" && (
          <>
            <Field label={tr("instrument")} className="min-w-[130px]">
              <select className={selectCls} value={instrumentId} disabled={busy}
                onChange={(event) => { setInstrumentId(event.target.value); setQuantity(""); }} aria-label={tr("instrument")}>
                <option value="">{tr("choose")}</option>
                {measuringInstruments.map((option) => (
                  <option key={option.id} value={option.id}>{option.label}</option>
                ))}
              </select>
            </Field>
            <Field label={tr("quantity")} className="min-w-[110px]">
              <select className={selectCls} value={quantity || quantityOptions[0]} disabled={busy}
                onChange={(event) => setQuantity(event.target.value)} aria-label={tr("quantity")}>
                {quantityOptions.map((option) => (
                  <option key={option} value={option}>{tr(`quantity.${option}`)}</option>
                ))}
              </select>
            </Field>
          </>
        )}
        {draft.action === "connect" && (
          <Field label={tr("tubing")} className="min-w-[130px]">
            <select className={selectCls} value={instrumentId} disabled={busy}
              onChange={(event) => setInstrumentId(event.target.value)} aria-label={tr("tubing")}>
              <option value="">{tr("choose")}</option>
              {tubingOptions.map((option) => (
                <option key={option.id} value={option.id}>{option.label}</option>
              ))}
            </select>
          </Field>
        )}
        {draft.action === "filter" &&
          vesselField(tr("filterInto"), apparatusId, setApparatusId, vesselId || draft.vesselId)}
        {draft.action === "wash" && (
          <Field label={tr("instrument")} className="min-w-[130px]">
            <select className={selectCls} value={instrumentId || draft.instrumentId || ""} disabled={busy}
              onChange={(event) => setInstrumentId(event.target.value)} aria-label={tr("instrument")}>
              <option value="">{tr("choose")}</option>
              {equipmentOptions.map((option) => (
                <option key={option.id} value={option.id}>{option.label}</option>
              ))}
            </select>
          </Field>
        )}
        {(draft.action === "aspirate" || draft.action === "dispense" || draft.action === "pour" || draft.action === "dispose") && (
          <Field label={tr("amount")} className="min-w-[150px]">
            <div className="flex items-center gap-1.5">
              <div className="flex gap-1">
                {presets.map((preset) => (
                  <button key={preset} type="button" disabled={busy}
                    onClick={() => setAmountMl(String(preset / 1000))}
                    className={`min-h-[32px] cursor-pointer rounded-md px-1.5 text-[10px] font-medium ${
                      amountMl === String(preset / 1000) ? "bg-accent-soft text-accent-strong" : "bg-surface-hover text-fg-secondary"
                    }`}>
                    {preset >= 1000 ? `${preset / 1000}` : `0.${preset / 100}`}
                  </button>
                ))}
              </div>
              <Input type="number" min={0} step={0.5} value={amountMl} disabled={busy}
                onChange={(event) => setAmountMl(event.target.value)}
                placeholder={defaultLoad ? String(defaultLoad / 1000) : "mL"}
                aria-label={tr("amountMl")} className="h-8 w-20 text-xs" />
            </div>
          </Field>
        )}
        {draft.action === "pour" && (
          <Field label={tr("rate")} className="min-w-[110px]">
            <select className={selectCls} value={rate} disabled={busy}
              onChange={(event) => setRate(event.target.value)} aria-label={tr("rate")}>
              <option value="slow">{tr("rateSlow")}</option>
              <option value="normal">{tr("rateNormal")}</option>
            </select>
          </Field>
        )}
        {draft.action === "heat" && (
          <Field label={`${tr("power")} ${power / 10}%`} className="min-w-[150px]">
            <input type="range" min={100} max={1000} step={50} value={power} disabled={busy}
              onChange={(event) => setPower(Number(event.target.value))}
              aria-label={tr("power")} className="h-8 w-full accent-[var(--color-accent,#2f6f6a)]" />
          </Field>
        )}
        {draft.action === "stir" && (
          <Field label={`${tr("speed")} ${speed / 10}%`} className="min-w-[150px]">
            <input type="range" min={100} max={1000} step={50} value={speed} disabled={busy}
              onChange={(event) => setSpeed(Number(event.target.value))}
              aria-label={tr("speed")} className="h-8 w-full accent-[var(--color-accent,#2f6f6a)]" />
          </Field>
        )}
        {(draft.action === "heat" || draft.action === "stir" || draft.action === "wait") && (
          <Field label={tr("duration")} className="min-w-[150px]">
            <div className="flex gap-1">
              {DURATION_PRESETS.map((preset) => (
                <button key={preset} type="button" disabled={busy}
                  onClick={() => setDurationMs(preset)}
                  className={`min-h-[32px] cursor-pointer rounded-md px-2 text-[10px] font-medium ${
                    durationMs === preset ? "bg-accent-soft text-accent-strong" : "bg-surface-hover text-fg-secondary"
                  }`}>
                  {preset / 1000}s
                </button>
              ))}
            </div>
          </Field>
        )}
        <Button size="sm" disabled={!canSubmit} onClick={submit} data-testid="chem-lab-operation-confirm">
          {tr("confirm")}
        </Button>
      </div>
      {draft.action === "dispose" && (
        <p className="mt-2 text-[11px] leading-4 text-muted">{tr("disposeNote")}</p>
      )}
    </div>
  );
}
