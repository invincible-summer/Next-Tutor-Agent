"use client";

/**
 * 器材旁的复用小气泡（plan §4.6 I1）：跟随 world anchor 每帧投影到屏幕，
 * 靠近视口边缘自动换边；动作按钮来自组合根（加入/倒出/点火熄灭/开关/转动/
 * 复制/移除/拆开）。rAF 循环只在挂载期间存在，直接写 transform 不触发 render。
 */
import { useEffect, useRef } from "react";
import * as THREE from "three";
import type { ChemSceneController } from "./scene/SceneController.ts";

export interface PopoverAction {
  id: string;
  label: string;
  danger?: boolean;
  /** 演示液色点（按钮前的圆点标记颜色）。 */
  dotColor?: string;
  onClick: () => void;
}

const EST_WIDTH = 190;
const EST_ROW = 38;

export function ObjectPopover(props: {
  controller: ChemSceneController;
  getAnchorWorld: () => THREE.Vector3 | null;
  title: string;
  subtitle?: string;
  actions: PopoverAction[];
  onClose: () => void;
}) {
  const cardRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let raf = 0;
    const projected = new THREE.Vector3();
    const project = () => {
      raf = requestAnimationFrame(project);
      const card = cardRef.current;
      if (!card) return;
      const anchor = props.getAnchorWorld();
      if (!anchor) return;
      const rect = props.controller.renderer.domElement.getBoundingClientRect();
      projected.copy(anchor).project(props.controller.camera);
      const x = ((projected.x + 1) / 2) * rect.width;
      const y = ((1 - projected.y) / 2) * rect.height;
      const height = 44 + props.actions.length * EST_ROW;
      // 边缘换边：默认在锚点上方展开，贴顶时翻到下方；水平居中并夹取在视口内。
      const flipBelow = y - height < 70;
      const top = flipBelow ? Math.min(y + 18, rect.height - height - 10) : Math.max(y - height - 12, 10);
      const left = Math.min(Math.max(x - EST_WIDTH / 2, 10), Math.max(10, rect.width - EST_WIDTH - 10));
      card.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
    };
    raf = requestAnimationFrame(project);
    return () => cancelAnimationFrame(raf);
    // 动作列表由父组件按文档重建；投影循环读取的是最新闭包。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.actions.length, props.controller]);

  return (
    <div
      ref={cardRef}
      className="chem-popover"
      role="dialog"
      aria-label={props.title}
      onPointerDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); props.onClose(); } }}
    >
      <p className="chem-popover-title">{props.title}</p>
      {props.subtitle && <p className="chem-popover-subtitle">{props.subtitle}</p>}
      <div className="chem-popover-actions" role="group">
        {props.actions.map((action) => (
          <button
            key={action.id}
            type="button"
            className={`chem-popover-btn${action.danger ? " danger" : ""}`}
            onClick={() => action.onClick()}
          >
            {action.dotColor && <span className="chem-popover-dot" style={{ background: action.dotColor }} aria-hidden="true" />}
            {action.label}
          </button>
        ))}
      </div>
    </div>
  );
}
