import type { ReactNode } from "react";

/** 回顾层分区头：无卡片边框，图标 + 标题 + 说明，靠排版与分隔线组织。 */
export function SectionHeader({
  icon,
  title,
  desc,
}: {
  icon?: ReactNode;
  title: ReactNode;
  desc?: ReactNode;
}) {
  return (
    <div className="mb-2 flex items-start gap-2">
      {icon && <span className="mt-0.5 text-muted">{icon}</span>}
      <div className="min-w-0">
        <h2 className="text-[0.8125rem] font-semibold text-fg-secondary">{title}</h2>
        {desc && <p className="mt-0.5 text-[0.6875rem] leading-relaxed text-muted">{desc}</p>}
      </div>
    </div>
  );
}

/** 紧凑空态：保留空态文案，但收敛「又大又空的虚线框」为安静的小块。 */
export function CompactEmpty({ title, desc }: { title: string; desc?: string }) {
  return (
    <div className="rounded-[8px] bg-surface-sunken/50 px-3 py-2.5">
      <p className="text-[0.75rem] text-fg-secondary">{title}</p>
      {desc && <p className="mt-0.5 text-[0.6875rem] leading-relaxed text-muted">{desc}</p>}
    </div>
  );
}
