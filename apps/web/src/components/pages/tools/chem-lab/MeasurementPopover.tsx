"use client";

/**
 * Instrument reading popover: latest reading + status from the RenderFrame,
 * plus an entry into the shared measure operation. Measurement is an action,
 * not a passive view — only a measure command produces a recorded reading.
 */
import { useMemo, type RefObject } from "react";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";
import { Button } from "@/components/ui/Button";
import { instrumentVisuals } from "./chem-lab-renderer";
import type { ChemLabDisplay } from "./useChemLabSession";
import type { OperationDraft } from "./interaction";

type AnyRecord = Record<string, unknown>;

function l10n(entry: unknown, language: string): string {
  if (entry && typeof entry === "object" && !Array.isArray(entry)) {
    const row = entry as AnyRecord;
    const value = row[language] ?? row.zh ?? row.en;
    if (typeof value === "string") return value;
  }
  return "";
}

export interface MeasurementPopoverProps {
  pack: AnyRecord | null;
  display: ChemLabDisplay;
  language: string;
  equipmentId: string | null;
  anchorRef: RefObject<HTMLElement | null>;
  open: boolean;
  busy: boolean;
  tr: (key: string, fallback?: string) => string;
  onClose: () => void;
  onOperate: (draft: OperationDraft) => void;
}

export function MeasurementPopover({
  pack,
  display,
  language,
  equipmentId,
  anchorRef,
  open,
  busy,
  tr,
  onClose,
  onOperate,
}: MeasurementPopoverProps) {
  const info = useMemo(() => {
    if (!equipmentId) return null;
    const equipmentDefs = (pack?._equipment_defs ?? pack?.equipment_defs ?? {}) as AnyRecord;
    const equipment = (display.engineState?.equipment ?? {}) as AnyRecord;
    const item = equipment[equipmentId] as AnyRecord | undefined;
    if (!item) return null;
    const kind = String(item.kind ?? "");
    const def = equipmentDefs[kind] as AnyRecord | undefined;
    const reading = instrumentVisuals(display.renderFrame).find((row) => row.equipmentId === equipmentId);
    const load = (item.load ?? {}) as AnyRecord;
    return {
      name: l10n(def?.name, language) || equipmentId,
      reading: reading?.reading ?? "",
      unit: reading?.unit ?? "",
      status: reading?.status ?? "idle",
      loadMl: Number(load.volume_uL ?? 0) / 1000,
      clean: Boolean(load.clean ?? true),
      connected: Boolean(item.connected),
    };
  }, [pack, display, equipmentId, language]);

  return (
    <AnchoredPopover anchorRef={anchorRef} open={open && Boolean(info)} onClose={onClose} placement="bottom-start">
      {info && (
        <div className="w-56 rounded-[12px] border border-border bg-surface p-3 shadow-lg" data-testid="chem-lab-measurement-popover">
          <p className="text-xs font-semibold text-fg">{info.name}</p>
          <div className="mt-2 flex items-baseline gap-1.5">
            {info.reading ? (
              <>
                <span className="tnum text-lg font-semibold text-accent-strong">{info.reading}</span>
                <span className="text-xs text-muted">{info.unit}</span>
              </>
            ) : (
              <span className="text-xs text-muted">{tr("noReading")}</span>
            )}
          </div>
          <p className="mt-1 text-[11px] text-muted">
            {[
              info.loadMl > 0 ? tr("loaded").replace("%n", info.loadMl.toFixed(1)) : "",
              info.clean ? "" : tr("dirty"),
              info.connected ? tr("connected") : "",
            ]
              .filter(Boolean)
              .join(" · ") || tr("ready")}
          </p>
          <Button
            size="sm"
            variant="outline"
            className="mt-3 w-full"
            disabled={busy}
            onClick={() => {
              if (equipmentId) onOperate({ action: "measure", instrumentId: equipmentId });
              onClose();
            }}
            data-testid="chem-lab-measure-from-popover"
          >
            {tr("measure")}
          </Button>
        </div>
      )}
    </AnchoredPopover>
  );
}
