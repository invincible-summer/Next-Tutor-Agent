"use client";

/**
 * 3D 视口容器（plan §2.1）：client-only 生命周期，持有唯一的
 * ChemSceneController + LabEnvironment；WebGL2 不可用或上下文丢失时给出
 * 明确降级说明与返回目录入口，绝不黑屏。
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { ChemSceneController } from "./scene/SceneController.ts";
import { LabEnvironment } from "./scene/Environment.ts";

export interface SceneBundle {
  controller: ChemSceneController;
  environment: LabEnvironment;
}

export function SceneViewport(props: {
  dark: boolean;
  onScene?: (bundle: SceneBundle | null) => void;
  onContextLost?: () => void;
  onContextRestored?: () => void;
  strings: { webglFailed: string; webglFailedHint: string; backToCatalog: string; contextLost: string };
  children?: ReactNode;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [bundle, setBundle] = useState<SceneBundle | null>(null);
  const [failed, setFailed] = useState(false);
  const [lost, setLost] = useState(false);
  const bundleRef = useRef<SceneBundle | null>(null);
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
    const next: SceneBundle = { controller, environment };
    bundleRef.current = next;
    // 调试/e2e 探针（只读）：质量档、阴影、相机与场景统计。
    (window as unknown as { __chemLabDebug?: unknown }).__chemLabDebug = {
      controller,
      environment,
      tier: controller.tier,
    };
    const readyTimer = window.setTimeout(() => {
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
    <div ref={hostRef} className="chem-scene-viewport">
      {lost && (
        <div className="chem-scene-lost" role="status">{props.strings.contextLost}</div>
      )}
      {props.children}
    </div>
  );
}
