"use client";

import Link from "next/link";
import { FlaskConical, Move3D, Sparkles } from "lucide-react";
import type { CSSProperties } from "react";
import { useUIStore } from "@/lib/store";
import { listStages, type StageSummary } from "@next-tutor/domain";
import "./chem-lab-3d.css";

const COPY = {
  zh: {
    eyebrow: "化学模拟实验台",
    title: "把器材放上台面，开始自由探索",
    intro: "这是一个没有步骤和评分的 3D 实验玩具。拖动玻璃器皿、接上软管、加演示液，让火焰、气泡、蒸汽和色彩自己发生。",
    enter: "进入实验台",
    free: "自由拼装",
    cues: "实时演出",
    auto: "可选 AUTO 预组装",
    hint: "点击器材架上的器材即可取用；进入台面后，点一下器材会出现就地气泡。",
  },
  en: {
    eyebrow: "CHEMISTRY SIMULATION BENCH",
    title: "Place the glassware. Follow the motion.",
    intro: "A 3D chemistry toy with no steps or score. Move vessels, connect tubes and add demo liquids to set bubbles, vapor, flame and color in motion.",
    enter: "Open bench",
    free: "Free assembly",
    cues: "Live effects",
    auto: "Optional AUTO setup",
    hint: "Click a piece on the rear rack to bring it onto the bench. Select any object for its nearby action bubble.",
  },
} as const;

function StageThumbnail({ stage }: { stage: StageSummary }) {
  return (
    <div className={`chem-catalog-thumbnail chem-catalog-motif-${stage.thumbnail.motif}`} style={{ "--stage-hue": `${stage.thumbnail.hue}` } as CSSProperties} aria-hidden="true">
      <span className="chem-catalog-glow" />
      <span className="chem-catalog-vessel vessel-a" />
      <span className="chem-catalog-vessel vessel-b" />
      <span className="chem-catalog-tube tube-a" />
      <span className="chem-catalog-tube tube-b" />
      <span className="chem-catalog-spark spark-a" />
      <span className="chem-catalog-spark spark-b" />
    </div>
  );
}

export function ChemLabCatalog() {
  const lang = useUIStore(s => s.lang);
  const copy = COPY[lang];
  const stages = listStages();
  return (
    <div className="chem-catalog" data-testid="chem-lab-catalog">
      <div className="chem-catalog-inner">
        <header className="chem-catalog-hero">
          <div className="chem-catalog-mark"><FlaskConical aria-hidden="true" /></div>
          <div>
            <p className="chem-catalog-eyebrow">{copy.eyebrow}</p>
            <h1>{copy.title}</h1>
            <p className="chem-catalog-intro">{copy.intro}</p>
          </div>
        </header>

        <div className="chem-catalog-pills" aria-label={copy.eyebrow}>
          <span><Move3D aria-hidden="true" />{copy.free}</span>
          <span><Sparkles aria-hidden="true" />{copy.cues}</span>
          <span>{copy.auto}</span>
        </div>

        <section className="chem-catalog-grid" aria-label={copy.eyebrow}>
          {stages.map(stage => (
            <Link
              key={stage.id}
              href={`/tools/lab/chemistry?stage=${encodeURIComponent(stage.id)}`}
              className="chem-catalog-card"
              data-testid={`chem-lab-stage-${stage.id}`}
            >
              <StageThumbnail stage={stage} />
              <div className="chem-catalog-card-body">
                <div className="chem-catalog-card-heading">
                  <h2>{stage.title[lang]}</h2>
                  <span aria-hidden="true">↗</span>
                </div>
                <p>{stage.description[lang]}</p>
                <span className="chem-catalog-card-action">{copy.enter}</span>
              </div>
            </Link>
          ))}
        </section>

        <p className="chem-catalog-hint">{copy.hint}</p>
      </div>
    </div>
  );
}
