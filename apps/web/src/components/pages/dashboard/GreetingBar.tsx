import type { Tr } from "./shared";

/** 问候区：M8 个性化问候语。
 *
 * streak 徽章已按 C14 收敛移除（顶栏火焰/统计卡/画像/编排四处保留，
 * 同一数据不在总览页重复展示两次）。 */
export function GreetingBar({
  greeting,
  tr,
}: {
  greeting: string | null;
  tr: Tr;
}) {
  return (
    <h1 className="font-serif text-xl font-semibold text-fg">
      {greeting ?? tr("greeting.fallback")}
    </h1>
  );
}
