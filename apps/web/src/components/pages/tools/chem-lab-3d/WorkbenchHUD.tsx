"use client";

/**
 * 56px 沉浸顶栏（plan §0/§2）：返回+关卡名 / AUTO / 保存/打开/重置 / ⌖视角。
 * 窄屏（≤640px）把次要操作收进 ⋯ 菜单，AUTO 与返回始终可见（plan §2 竖屏）。
 * 顶栏不随拖动重排：纯静态按钮，无逐帧状态。
 */
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Crosshair, FolderOpen, MoreHorizontal, RotateCcw, Save, Sparkles } from "lucide-react";

export function WorkbenchHUD(props: {
  backHref: string;
  stageTitle: string;
  dirty: boolean;
  strings: {
    back: string;
    auto: string;
    save: string;
    open: string;
    reset: string;
    resetCamera: string;
    more: string;
  };
  /** dirty 时由组合根拦截确认（EXIT1）；未提供则直接导航。 */
  onBack?: () => void;
  onAuto: () => void;
  onSave: () => void;
  onOpen: () => void;
  onReset: () => void;
  onResetCamera: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: PointerEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [menuOpen]);

  const secondary = (
    <>
      <button type="button" className="chem-hud-btn" onClick={props.onSave} title={props.strings.save}>
        <Save aria-hidden="true" /><span>{props.strings.save}</span>
      </button>
      <button type="button" className="chem-hud-btn" onClick={props.onOpen} title={props.strings.open}>
        <FolderOpen aria-hidden="true" /><span>{props.strings.open}</span>
      </button>
      <button type="button" className="chem-hud-btn" onClick={props.onReset} title={props.strings.reset}>
        <RotateCcw aria-hidden="true" /><span>{props.strings.reset}</span>
      </button>
      <button type="button" className="chem-hud-btn" onClick={props.onResetCamera} title={props.strings.resetCamera}>
        <Crosshair aria-hidden="true" /><span>{props.strings.resetCamera}</span>
      </button>
    </>
  );

  return (
    <header className="chem-hud" role="toolbar" aria-label={props.stageTitle}>
      <Link
        className="chem-hud-back"
        href={props.backHref}
        aria-label={props.strings.back}
        onClick={(e) => { if (props.onBack) { e.preventDefault(); props.onBack(); } }}
      >
        <ArrowLeft aria-hidden="true" />
      </Link>
      <p className="chem-hud-title">{props.stageTitle}{props.dirty ? <span className="chem-hud-dirty" aria-hidden="true">•</span> : null}</p>
      <div className="chem-hud-actions">{secondary}</div>
      <button type="button" className="chem-hud-auto" onClick={props.onAuto}>
        <Sparkles aria-hidden="true" />{props.strings.auto}
      </button>
      <div className="chem-hud-more" ref={menuRef}>
        <button
          type="button"
          className="chem-hud-btn icon-only"
          aria-expanded={menuOpen}
          aria-label={props.strings.more}
          onClick={() => setMenuOpen(v => !v)}
        >
          <MoreHorizontal aria-hidden="true" />
        </button>
        {menuOpen && (
          <div className="chem-hud-menu" role="menu">
            <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); props.onSave(); }}>
              <Save aria-hidden="true" /><span>{props.strings.save}</span>
            </button>
            <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); props.onOpen(); }}>
              <FolderOpen aria-hidden="true" /><span>{props.strings.open}</span>
            </button>
            <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); props.onReset(); }}>
              <RotateCcw aria-hidden="true" /><span>{props.strings.reset}</span>
            </button>
            <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); props.onResetCamera(); }}>
              <Crosshair aria-hidden="true" /><span>{props.strings.resetCamera}</span>
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
