import { BookOpen, Layers, NotebookPen, Presentation } from "lucide-react";
import type { LandingTr } from "./LandingNav";
import styles from "./landing.module.css";

/** 静态能力索引，让首屏与功能区之间保持安静的阅读节奏。 */
export function Marquee({ tr }: { tr: LandingTr }) {
  return <div className={styles.capabilityStrip}>{[BookOpen, Presentation, Layers, NotebookPen].map((Icon, index) => <span key={index}><Icon size={17} strokeWidth={1.4} />{tr(`landing.marquee.${index + 1}`)}</span>)}</div>;
}
