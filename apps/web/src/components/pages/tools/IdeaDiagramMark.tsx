/** 原创几何标记：一枚想法（左上菱形火花）与画布中生长的节点连线，
 *  表达「想法落入画布，长成清晰的教学图示」。 */
export function IdeaDiagramMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
      strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M4 1.8 6.2 4 4 6.2 1.8 4Z" />
      <rect x="7.5" y="7.5" width="14.5" height="14.5" rx="3.2" />
      <path d="M11 17.5h4v-4.5h4.5" />
      <circle cx="11" cy="17.5" r="1.3" fill="currentColor" stroke="none" />
      <circle cx="19.5" cy="13" r="1.3" fill="currentColor" stroke="none" />
    </svg>
  );
}
