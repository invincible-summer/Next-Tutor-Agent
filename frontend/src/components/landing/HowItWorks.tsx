import { Reveal } from "./Reveal";
import type { LandingTr } from "./LandingNav";
import styles from "./landing.module.css";

export function HowItWorks({ tr }: { tr: LandingTr }) {
  return (
    <section id="how" className={styles.section}>
      <Reveal className={styles.sectionHeading}><div><p className={styles.eyebrow}>{tr("landing.how.kicker")}</p><h2>{tr("landing.how.title")}</h2></div><p>{tr("landing.how.subtitle")}</p></Reveal>
      <div className={styles.steps}>{["s1", "s2", "s3"].map((key, index) => (
        <Reveal key={key} delay={index * 80} className={styles.step}><span>0{index + 1}</span><h3>{tr(`landing.how.${key}.title`)}</h3><p>{tr(`landing.how.${key}.desc`)}</p></Reveal>
      ))}</div>
    </section>
  );
}
