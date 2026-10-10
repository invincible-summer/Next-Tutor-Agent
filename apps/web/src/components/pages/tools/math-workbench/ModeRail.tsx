"use client";

/**
 * Left primary tool rail (52px, always visible) plus the secondary mode
 * drawer (default closed; overlay so it never steals stage width — plan D4).
 * Three modes, each with its own canvas and document (ADR-0022); tool
 * entries switch with the active drawing mode.
 */

import { useEffect, useRef } from "react";
import type { DrawingMode } from "./workbench-types.ts";

interface ToolDef { id: string; labelKey: string; glyph: string }

const TOOLS_2D_FUNCTIONS: readonly ToolDef[] = [
  { id: "select", labelKey: "toolSelect", glyph: "⬚" },
  { id: "point", labelKey: "toolPoint", glyph: "·" },
  { id: "measure", labelKey: "toolMeasure", glyph: "≡" },
];

const TOOLS_2D_GEOMETRY: readonly ToolDef[] = [
  { id: "select", labelKey: "toolSelect", glyph: "⬚" },
  { id: "point", labelKey: "toolPoint", glyph: "·" },
  { id: "line", labelKey: "toolLine", glyph: "／" },
  { id: "circle", labelKey: "toolCircle", glyph: "○" },
  { id: "measure", labelKey: "toolMeasure", glyph: "≡" },
  { id: "text", labelKey: "toolText", glyph: "Ｔ" },
];

const TOOLS_3D_FUNCTIONS: readonly ToolDef[] = [
  { id: "select", labelKey: "toolSelect", glyph: "⬚" },
];

function toolsForMode(mode: DrawingMode): readonly ToolDef[] {
  switch (mode) {
    case "functions2d": return TOOLS_2D_FUNCTIONS;
    case "geometry2d": return TOOLS_2D_GEOMETRY;
    case "functions3d": return TOOLS_3D_FUNCTIONS;
  }
}

export function ModeRail({
  tr, mode, tool, modePanelOpen, onToggleModePanel, onCloseModePanel, onSelectMode, onSelectTool, onHelp,
}: {
  tr: (key: string) => string;
  mode: DrawingMode;
  tool: string;
  modePanelOpen: boolean;
  onToggleModePanel: () => void;
  onCloseModePanel: () => void;
  onSelectMode: (mode: DrawingMode) => void;
  onSelectTool: (tool: string) => void;
  onHelp: () => void;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!modePanelOpen) return;
    const panel = panelRef.current;
    panel?.querySelector<HTMLElement>("[data-mode-entry]")?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onCloseModePanel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [modePanelOpen, onCloseModePanel]);

  const modes: Array<{ id: DrawingMode; labelKey: string; descKey: string; glyph: string }> = [
    { id: "functions2d", labelKey: "modeFunctions2d", descKey: "modeFunctions2dDesc", glyph: "∿" },
    { id: "geometry2d", labelKey: "modeGeometry2d", descKey: "modeGeometry2dDesc", glyph: "△" },
    { id: "functions3d", labelKey: "modeFunctions3d", descKey: "modeFunctions3dDesc", glyph: "≋" },
  ];

  return (
    <>
      <nav className="mode-rail" aria-label={tr("modeToggle")}>
        <button
          className={`rail-button ${modePanelOpen ? "is-active" : ""}`}
          onClick={onToggleModePanel}
          aria-expanded={modePanelOpen}
          aria-haspopup="true"
          aria-label={`${tr("modeToggle")}：${tr(modeLabelKey(mode))}`}
          data-testid="geometry-mode-toggle"
          title={tr("modeToggle")}
        >▦</button>
        <div className="rail-divider" />
        {toolsForMode(mode).map((entry) => (
          <button
            key={entry.id}
            className={`rail-button ${tool === entry.id ? "is-active" : ""}`}
            onClick={() => onSelectTool(entry.id)}
            aria-label={tr(entry.labelKey)}
            aria-pressed={tool === entry.id}
            title={tr(entry.labelKey)}
            data-testid={`geometry-tool-${entry.id}`}
          >{entry.glyph}</button>
        ))}
        <div className="rail-spacer" />
        <button className="rail-button" title={tr("helpTitle")} aria-label={tr("helpTitle")} onClick={onHelp}>?</button>
      </nav>
      {modePanelOpen && (
        <div className="mode-overlay" onClick={onCloseModePanel} aria-hidden />
      )}
      <div
        ref={panelRef}
        className={`mode-panel ${modePanelOpen ? "is-open" : ""}`}
        data-testid="geometry-mode-panel"
        role="dialog"
        aria-label={tr("modePanelTitle")}
        aria-hidden={!modePanelOpen}
      >
        <div className="mode-panel-title">{tr("modePanelTitle")}</div>
        {modes.map((entry) => (
          <button
            key={entry.id}
            data-mode-entry={entry.id}
            className={`mode-entry ${mode === entry.id ? "is-current" : ""}`}
            onClick={() => onSelectMode(entry.id)}
            data-testid={`geometry-mode-${entry.id}`}
            aria-current={mode === entry.id}
          >
            <span className="mode-glyph" aria-hidden>{entry.glyph}</span>
            <span className="mode-text">
              <span className="mode-name">{tr(entry.labelKey)}</span>
              <span className="mode-desc">{tr(entry.descKey)}</span>
            </span>
            {mode === entry.id && <span className="mode-current-mark" aria-hidden>✓</span>}
          </button>
        ))}
      </div>
    </>
  );
}

function modeLabelKey(mode: DrawingMode): string {
  switch (mode) {
    case "functions2d": return "modeFunctions2d";
    case "geometry2d": return "modeGeometry2d";
    case "functions3d": return "modeFunctions3d";
  }
}
