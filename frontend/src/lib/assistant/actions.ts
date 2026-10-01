// 助手受控动作客户端（plan.md §9.3/§9.4/§19.3/§19.4，A10）。
// execute → 本地执行页面命令 → ack；ack_token 只在内存中传递，
// 不写入任何持久存储或模型上下文。
"use client";

import {
  ackAssistantAction,
  approveAssistantAction,
  executeAssistantAction,
  getAssistantActionPreview,
} from "./api";
import { activeAdapter } from "./page-context";
import { resolveTargetUrl, urlMatchesTarget } from "./routes";
import type {
  ActionApproveResponse,
  ActionExecutionResponse,
  ActionPreview,
  AssistantAction,
  ClassroomQuestionCommand,
  PageCommand,
  PageCommandResult,
} from "./types.generated";

/** 每标签页一个稳定 client_instance_id（§9.6，sessionStorage）。 */
export function clientInstanceId(): string {
  const KEY = "edu-assistant-instance";
  try {
    let id = sessionStorage.getItem(KEY);
    if (!id) {
      id = `tab-${crypto.randomUUID().replace(/-/g, "").slice(0, 16)}`;
      sessionStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    return `tab-anon-${Math.random().toString(36).slice(2, 12)}`;
  }
}

export async function executeAction(
    actionId: string, invocationId: string, routeEpoch: number,
    approvalId?: string): Promise<ActionExecutionResponse> {
  return executeAssistantAction(actionId, {
    invocation_id: invocationId,
    client_instance_id: clientInstanceId(),
    route_epoch: routeEpoch,
    ...(approvalId ? { approval_id: approvalId } : {}),
  });
}

// --- 预览与审批（§21.4）---------------------------------------------------

export function fetchActionPreview(actionId: string): Promise<ActionPreview> {
  return getAssistantActionPreview(actionId);
}

export function approvePreview(
    actionId: string, preview: ActionPreview,
    decision: "approve" | "reject"): Promise<ActionApproveResponse> {
  return approveAssistantAction(actionId, {
    preview_id: preview.preview_id,
    parameter_hash: preview.parameter_hash,
    decision,
  });
}

export async function ackAction(
    actionId: string, commandId: string, ackToken: string,
    result: PageCommandResult): Promise<ActionExecutionResponse> {
  return ackAssistantAction(actionId, {
    command_id: commandId,
    ack_token: ackToken,
    result: result.status,
    error_code: result.code ?? null,
  });
}

let navigationSequence = 0;

/** 本地执行页面命令（§9.5/§19.5）。返回执行结果供 ack。 */
export async function runPageCommand(
    command: PageCommand,
    router: { push: (url: string) => unknown },
): Promise<PageCommandResult> {
  const sequence = ++navigationSequence;
  // 页面保护：编辑脏态/进行中活动时只取消本次前端执行（§9.3）。
  const adapter = activeAdapter();
  if (adapter) {
    try {
      const verdict = await adapter.beforeNavigate();
      if (verdict === "stay") {
        return { status: "cancelled", code: "user_stayed" };
      }
    } catch {
      return { status: "failed", code: "page_not_ready" };
    }
  }
  switch (command.kind) {
    case "navigate": {
      const url = resolveTargetUrl(command.target);
      if (!url) {
        return { status: "failed", code: "page_not_ready" };
      }
      if (sequence !== navigationSequence) return { status: "cancelled", code: "user_stayed" };
      await router.push(url);
      // 在服务端 15 秒 ack 窗口内等待页面完成；不能以 URL 变化代替实体就绪。
      let reached = false;
      for (let i = 0; i < 240; i += 1) {
        await new Promise((r) => setTimeout(r, 50));
        if (sequence !== navigationSequence) return { status: "cancelled", code: "user_stayed" };
        const matches = urlMatchesTarget(window.location.href, new URL(url, window.location.origin).href);
        if (!matches) {
          if (reached) return { status: "cancelled", code: "user_stayed" };
          continue;
        }
        reached = true;
        const destination = activeAdapter();
        if (!destination) continue;
        try {
          const result = destination.navigationStatus(command.target);
          if (result) return result;
        } catch {
          return { status: "failed", code: "page_not_ready" };
        }
      }
      return { status: "failed", code: "page_not_ready" };
    }

    case "workspace_form": {
      // A11/A13 接入工作区壳内 useWsSettings.open；当前先落到 /chat。
      const url = !command.workspace_id || command.workspace_id === "new"
        ? "/chat"
        : `/chat?ws=${encodeURIComponent(command.workspace_id)}`;
      await router.push(url);
      return { status: "succeeded" };
    }
    case "lesson_form":
    case "chat_draft":
    case "note_draft":
    case "classroom_question": {
      // §19.5 每类命令的确定落点：lesson_form→/course、chat_draft→/chat、
      // note_draft→/notes（临时编辑器）、classroom_question→目标 run 播放页。
      const draftId = "draft_id" in command ? command.draft_id : "";
      let url: string;
      if (command.kind === "lesson_form") {
        url = `/course?assistant_draft=${encodeURIComponent(draftId)}`;
      } else if (command.kind === "chat_draft") {
        url = `/chat?assistant_draft=${encodeURIComponent(draftId)}`;
      } else if (command.kind === "note_draft") {
        url = `/notes?assistant_draft=${encodeURIComponent(draftId)}`;
      } else {
        // 生成类型中 kind 为可选判别，剩余分支固定为 classroom_question。
        const q = command as ClassroomQuestionCommand;
        url = `/workspaces/${encodeURIComponent(q.workspace_id)}`
          + `/classroom/${encodeURIComponent(q.lesson_id)}`
          + `/learn/${encodeURIComponent(q.run_id)}`
          + `?assistant_draft=${encodeURIComponent(draftId)}`;
      }
      await router.push(url);
      return { status: "succeeded" };
    }
    default:
      return { status: "failed", code: "page_not_ready" };
  }
}

/** §19.3/§9.2：自动执行资格检查（当前活跃标签页 + 可见面 + 面板展开）。 */
export function autoExecuteEligible(panelOpen: boolean): boolean {
  if (!panelOpen) return false;          // 面板收起不自动换页
  if (typeof document !== "undefined"
      && document.visibilityState === "hidden") {
    return false;                        // 标签页隐藏不自动
  }
  return true;
}

/** 从动作列表挑首个可自动执行的候选（proposed + automatic）。 */
export function automaticCandidate(
  actions: AssistantAction[],
): AssistantAction | null {
  return actions.find((a) => a.execution === "automatic"
    && (a.state ?? "proposed") === "proposed") ?? null;
}
