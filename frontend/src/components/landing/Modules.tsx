import { ArrowDown, Check, FileText, Network } from "lucide-react";
import { Reveal } from "./Reveal";
import type { LandingTr } from "./LandingNav";
import styles from "./landing.module.css";

export function Modules({ tr }: { tr: LandingTr }) {
  return (
    <section id="modules" className={styles.principles}>
      <div className={styles.principleInner}>
        <Reveal className={styles.evidenceArt}>
          <p className={styles.eyebrow}>{tr("landing.modules.artLabel")}</p>
          <div className={styles.sourceCard}><FileText size={21} /><div><strong>{tr("landing.modules.source")}</strong><p>{tr("landing.modules.sourceDesc")}</p></div><span>01</span></div>
          <ArrowDown className={styles.flowArrow} size={20} />
          <div className={styles.evidenceCenter}><Network size={28} strokeWidth={1.4} /><h3>{tr("landing.modules.center")}</h3><p>{tr("landing.modules.centerDesc")}</p><div><span>{tr("landing.modules.chip1")}</span><span>{tr("landing.modules.chip2")}</span><span>{tr("landing.modules.chip3")}</span></div></div>
          <ArrowDown className={styles.flowArrow} size={20} />
          <div className={styles.sourceCard}><Check size={21} /><div><strong>{tr("landing.modules.next")}</strong><p>{tr("landing.modules.nextDesc")}</p></div><span>03</span></div>
          <p className={styles.artCaption}>{tr("landing.modules.caption")}</p>
        </Reveal>
        <Reveal className={styles.principleCopy}>
          <p className={styles.eyebrow}>{tr("landing.modules.kicker")}</p>
          <h2>{tr("landing.modules.title")}</h2><p className={styles.intro}>{tr("landing.modules.subtitle")}</p>
          {["1", "2", "3"].map((key) => <div className={styles.principleRow} key={key}><span>0{key}</span><div><h3>{tr(`landing.modules.${key}.title`)}</h3><p>{tr(`landing.modules.${key}.desc`)}</p></div></div>)}
        </Reveal>
      </div>
    </section>
  );
}
