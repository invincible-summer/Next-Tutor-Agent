"use client";

// 统计事实卡（plan.md §7.3/§11.4，A06 视觉；数字由服务端确定性生成）。
import { useAssistantStore } from "@/lib/assistant/store";
import { stringsFor } from "../strings";
import type { MetricItem } from "@/lib/assistant/types.generated";

export function MetricsCard({ items }: { items: MetricItem[] }) {
  const lang = useAssistantStore((s) => s.lang);
  const t = stringsFor(lang);
  return (
    <div className="assistant-card assistant-metrics-card">
      <div className="assistant-metrics-grid">
        {items.map((item) => (
          <div key={item.key} className="assistant-metric"
            data-completeness={item.completeness}>
            <span className="assistant-metric-label">{item.label}</span>
            <span className="assistant-metric-value">
              {item.value === null
                ? (item.known_minimum !== null && item.known_minimum !== undefined
                    ? `${t.knownMinimumPrefix} ${fmt(item.known_minimum ?? 0)}${item.unit}`
                    : t.metricsUnavailable)
                : `${fmt(item.value ?? 0)}${item.unit}`}
            </span>
            {item.completeness !== "complete" && (
              <span className="assistant-metric-note">
                {item.unavailable_reason ?? ""}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function fmt(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}
