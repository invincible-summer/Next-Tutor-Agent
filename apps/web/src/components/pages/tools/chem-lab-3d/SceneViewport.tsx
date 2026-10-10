"use client";

/**
 * 统一 canvas 容器（chem-lab architecture）：client-only 生命周期持有者。
 * host 节点只包含 WebGL canvas——交互层在 host 上挂捕获监听并独占对象手势，
 * HUD/气泡/提示等 DOM 覆盖层必须是 host 的兄弟节点，避免把 UI 点击当成
 * 场景指针事件。WebGL2 不可用→降级说明+返回目录；contextlost 短提示。
 */
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ChemSceneController, type QualityTier } from "./scene/SceneController.ts";
import { LabEnvironment } from "./scene/Environment.ts";

export interface SceneBundle {
  controller: ChemSceneController;
  environment: LabEnvironment;
  tier: QualityTier;
}

export function SceneViewport(props: {
  dark: boolean;
  onScene?: (bundle: SceneBundle | null) => void;
  onContextLost?: () => void;
  onContextRestored?: () => void;
  strings: { webglFailed: string; webglFailedHint: string; backToCatalog: string; contextLost: string; loading: string };
  children?: React.ReactNode;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const bundleRef = useRef<SceneBundle | null>(null);
  const [bundle, setBundle] = useState<SceneBundle | null>(null);
  const [failed, setFailed] = useState(false);
  const [lost, setLost] = useState(false);
  const { onScene, onContextLost, onContextRestored } = props;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    if (!ChemSceneController.webgl2Available()) {
      // 与 math-workbench Stage3D 相同的延迟 setState 模式（lint 合规）。
      const failTimer = window.setTimeout(() => setFailed(true), 0);
      return () => window.clearTimeout(failTimer);
    }
    const controller = new ChemSceneController(host, {
      onContextLost: () => {
        setLost(true);
        onContextLost?.();
      },
      onContextRestored: () => {
        setLost(false);
        onContextRestored?.();
      },
    });
    const environment = new LabEnvironment(controller.tier);
    environment.attach(controller.scene);
    const next: SceneBundle = { controller, environment, tier: controller.tier };
    // 调试/e2e 探针（只读）：质量档、阴影、相机与场景统计。
    (window as unknown as { __chemLabDebug?: unknown }).__chemLabDebug = {
      controller,
      environment,
      tier: controller.tier,
    };
    const readyTimer = window.setTimeout(() => {
      bundleRef.current = next;
      setBundle(next);
      onScene?.(next);
    }, 0);
    return () => {
      window.clearTimeout(readyTimer);
      bundleRef.current = null;
      delete (window as unknown as { __chemLabDebug?: unknown }).__chemLabDebug;
      onScene?.(null);
      setBundle(null);
      environment.dispose();
      controller.dispose();
    };
    // 生命周期只在挂载时建立一次；回调经 ref 语义由调用方保证稳定。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const current = bundleRef.current;
    if (!current) return;
    current.environment.applyTheme(props.dark);
    // 玻璃/金属的环境反射基准随主题收放：暗色下保持轮廓可辨。
    current.controller.scene.environmentIntensity = props.dark ? 0.32 : 0.55;
    current.controller.invalidate();
  }, [bundle, props.dark]);

  if (failed) {
    return (
      <div className="chem-scene-fallback" role="alert">
        <div className="chem-scene-fallback-card">
          <p className="chem-scene-fallback-title">{props.strings.webglFailed}</p>
          <p className="chem-scene-fallback-hint">{props.strings.webglFailedHint}</p>
          <Link className="chem-scene-fallback-link" href="/tools/lab">{props.strings.backToCatalog}</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="chem-scene-viewport">
      <div ref={hostRef} className="chem-scene-host" />
      {lost && (
        <div className="chem-scene-lost" role="status">{props.strings.contextLost}</div>
      )}
      {bundle ? props.children : (
        <div className="chem-workbench-loading" aria-busy="true">{props.strings.loading}</div>
      )}
    </div>
  );
}
