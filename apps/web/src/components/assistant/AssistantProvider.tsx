"use client";

// 助手全局 Provider（plan.md §5.1，A07）：根布局挂载的唯一客户端壳。
// children 保持在原位置；助手经 Host 的 Portal 渲染，不改变主页面宽度。
// 语言跟随站点 i18n；身份切换清理在 Host 内订阅 auth store 完成。
import { useEffect } from "react";
import { useUIStore } from "@/lib/store";
import { useAssistantStore } from "@/lib/assistant/store";
import { AssistantHost } from "./AssistantHost";
import { DEMO_MODE } from "@/lib/demo";

export function AssistantProvider({ children }: {
  children: React.ReactNode;
}) {
  const lang = useUIStore((s) => s.lang);
  const setLang = useAssistantStore((s) => s.setLang);

  useEffect(() => {
    setLang(lang);
  }, [lang, setLang]);

  return (
    <>
      {children}
      {!DEMO_MODE && <AssistantHost />}
    </>
  );
}
