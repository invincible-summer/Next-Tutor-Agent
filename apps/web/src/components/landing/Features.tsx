import Link from "next/link";
import { ArrowUpRight, BookOpen, MessageCircle, NotebookPen, Presentation, Route, ScanLine } from "lucide-react";
import { Reveal } from "./Reveal";
import type { LandingTr } from "./LandingNav";
import styles from "./landing.module.css";

const FEATURES = [
  { key: "f1", icon: MessageCircle }, { key: "f2", icon: Presentation },
  { key: "f3", icon: ScanLine }, { key: "f4", icon: NotebookPen },
  { key: "f5", icon: BookOpen }, { key: "f6", icon: Route },
] as const;

export function Features({ tr }: { tr: LandingTr }) {
  return (
    <section data-landing-snap id="features" className={styles.section}>
      <Reveal className={styles.sectionHeading}>
        <div><p className={styles.eyebrow}>{tr("landing.features.kicker")}</p><h2>{tr("landing.features.title")}</h2></div>
        <p>{tr("landing.features.subtitle")}</p>
      </Reveal>
      <div className={styles.featureGrid}>
        {FEATURES.map(({ key, icon: Icon }, index) => (
          <Reveal key={key} delay={index * 45} className={`${styles.feature} ${index < 2 ? styles.featureLead : ""} ${index === 1 ? styles.featureAccent : ""}`}>
            <div className={styles.featureTop}><Icon size={23} strokeWidth={1.4} /><span>0{index + 1}</span></div>
            <h3>{tr(`landing.features.${key}.title`)}</h3>
            <p>{tr(`landing.features.${key}.desc`)}</p>
            {index === 0 && (
              <div className={styles.chatPreview} aria-label={tr("landing.features.example")}>
                <p className={styles.previewLabel}>{tr("landing.features.example")}</p>
                <div className={styles.questionBubble}>{tr("landing.features.question")}</div>
                <div className={styles.answerBubble}><span>N</span><p>{tr("landing.features.answer")}</p></div>
                <span className={styles.previewSource}><BookOpen size={12} />{tr("landing.features.source")}</span>
              </div>
            )}
            {index === 1 && (
              <div className={styles.lessonPreview} aria-label={tr("landing.features.lessonExample")}>
                <div className={styles.slideRail}><span>01</span><span>02</span><span>03</span></div>
                <div className={styles.slide}>
                  <p>01 / {tr("landing.features.lessonExample")}</p>
                  <h4>{tr("landing.features.lessonTitle")}</h4>
                  <svg viewBox="0 0 280 90" aria-hidden="true">
                    <path d="M20 73H264M42 83V9" stroke="currentColor" strokeOpacity=".35" fill="none" />
                    <path d="M60 65Q150 78 234 12" stroke="currentColor" strokeWidth="2" fill="none" />
                    <path d="M116 82L235 15" stroke="#c47c50" strokeWidth="1.5" />
                    <circle cx="173" cy="50" r="4" fill="currentColor" />
                    <text x="191" y="70" fill="currentColor" fontSize="12" fontFamily="Georgia,serif">f′(x)</text>
                  </svg>
                  <div className={styles.slideFooter}><span>{tr("landing.features.lessonTopic")}</span><span>01 / 08</span></div>
                </div>
              </div>
            )}
            <div className={styles.featureDetail}>{tr(`landing.features.${key}.detail`)}</div>
          </Reveal>
        ))}
      </div>
      <div className={styles.sectionFoot}><p>{tr("landing.features.extra")}</p><Link href="/docs" className={styles.textLink}>{tr("landing.features.guide")}<ArrowUpRight size={16} /></Link></div>
    </section>
  );
}
