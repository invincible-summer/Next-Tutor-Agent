"use client";

/**
 * 工作台组合根（plan §2/§7.1）：关联当前关卡、文档、选择、dirty。
 * Phase B 骨架——渲染关卡的 AUTO 预组装模板（静止、熄灭、空瓶）作为
 * 视觉验收场景；交互/AUTO 按钮/存档在后续阶段接入同一组合根。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { applyAuto, createStageDocument, getStage } from "@next-tutor/domain";
import { STRINGS } from "./strings.ts";
import { SceneViewport, type SceneBundle } from "./SceneViewport.tsx";
import { SceneSync } from "./scene/SceneSync.ts";
import "./chem-lab-3d.css";

export function LabWorkbench(props: { stageId: string }) {
  const lang = useUIStore((s) => s.lang);
  const dark = useUIStore((s) => s.theme === "dark");
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);
  const stage = getStage(props.stageId);

  const [bundle, setBundle] = useState<SceneBundle | null>(null);
  const syncRef = useRef<SceneSync | null>(null);

  useEffect(() => {
    if (!bundle || !stage) return;
    const sync = new SceneSync(bundle.controller.tier);
    syncRef.current = sync;
    bundle.controller.root.add(sync.root, sync.tubes.group);
    // Phase B 预览：直接载入 AUTO 模板（空瓶、熄灭、泵停）做视觉验收。
    const doc = applyAuto(createStageDocument(stage), stage);
    sync.syncDocument(doc);
    bundle.controller.applyCameraPose(stage.assembledTemplate.camera ?? stage.camera);
    bundle.controller.invalidate();
    return () => {
      sync.dispose();
      syncRef.current = null;
    };
  }, [bundle, stage]);

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

  return (
    <div className="chem-workbench">
      <SceneViewport
        dark={dark}
        onScene={setBundle}
        strings={{
          webglFailed: tr("chem3d.webglFailed"),
          webglFailedHint: tr("chem3d.webglFailedHint"),
          backToCatalog: tr("chem3d.backToCatalog"),
          contextLost: tr("chem3d.contextLost"),
        }}
      >
        {bundle ? (
          <a className="chem-workbench-back" href="/tools/lab">← {tr("chem3d.back")}</a>
        ) : (
          <div className="chem-workbench-loading" aria-busy="true">{tr("chem3d.loading")}</div>
        )}
      </SceneViewport>
    </div>
  );
}
