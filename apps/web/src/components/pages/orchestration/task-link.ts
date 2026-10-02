// 任务 → 对话的启动链路（W4/A12）。旧版只拼 /chat?q=文案&send=1 纯文本深链：
// 计划上下文丢失、完成只能按概念名匹配今日全部任务。现在先经服务端 launch
// 绑定 task→episode→session（作答归因到「该任务」），再沿用同一套 kind 感知
// 首发消息；launch 失败（任务已完成/被删）回退纯文本深链（§9.4 仍支持）。
import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { launchOrchTask } from "@/lib/api-modules";
import type { OrchDailyTask } from "@/lib/types-modules";

type Tr = (key: string, fallback?: string) => string;

/** 渲染名：自定义标题优先，其次概念名，最后兜底「总结」。 */
export function taskDisplayName(task: OrchDailyTask, tr: Tr): string {
  return task.title.trim() || task.concept_name || tr("today.kind.summary");
}

/** kind 感知对话消息；无概念的自定义任务直接发标题。 */
export function taskChatMessage(task: OrchDailyTask, tr: Tr): string {
  const name = task.concept_name.trim();
  if (!name) return task.title.trim() || tr("task.msg.summary");
  switch (task.kind) {
    case "review":
      return tr("task.msg.review").replace("%c", name);
    case "practice":
      return tr("task.msg.practice").replace("%c", name);
    case "summary":
      return tr("task.msg.summary");
    default:
      return tr("task.msg.study").replace("%c", name);
  }
}

export function taskChatHref(task: OrchDailyTask, tr: Tr): string {
  return `/chat?q=${encodeURIComponent(taskChatMessage(task, tr))}&send=1`;
}

/** 绑定会话的跳转：launch_url + kind 感知首发消息（auto-send 进同一会话）。 */
export function taskLaunchHref(launchUrl: string, task: OrchDailyTask, tr: Tr): string {
  return `${launchUrl}?q=${encodeURIComponent(taskChatMessage(task, tr))}&send=1`;
}

/** kind 感知的行动按钮文案（去学 / 去复习 / 去练 / 去总结）。 */
export function taskGoLabel(task: OrchDailyTask, tr: Tr): string {
  return tr(`task.go.${task.kind}`, tr("today.go"));
}

/** 任务启动：launch → 跳转绑定会话；失败回退纯文本深链。
 * 返回 launchingId 供按钮禁用态使用。TodayCard 行与 kickoff CTA 共用。 */
export function useTaskLaunch(tr: Tr): {
  launch: (task: OrchDailyTask) => Promise<void>;
  launchingId: string | null;
} {
  const router = useRouter();
  const [launchingId, setLaunchingId] = useState<string | null>(null);
  const launch = useCallback(
    async (task: OrchDailyTask) => {
      setLaunchingId((cur) => cur ?? task.id);
      try {
        const r = await launchOrchTask(task.id);
        router.push(taskLaunchHref(r.launch_url, task, tr));
      } catch {
        router.push(taskChatHref(task, tr));
      } finally {
        setLaunchingId(null);
      }
    },
    [router, tr],
  );
  return { launch, launchingId };
}
