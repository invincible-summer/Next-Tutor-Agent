"use client";
import { useCallback, useEffect, useState } from "react";
import { useUIStore } from "@/lib/store";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { DEMO_MODE } from "@/lib/demo";

export function DemoReadOnlyDialog() {
  const lang = useUIStore((s) => s.lang);
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  useEffect(() => {
    if (!DEMO_MODE) return;
    const explain = () => setOpen(true);
    window.addEventListener("edu-demo-readonly", explain);
    return () => window.removeEventListener("edu-demo-readonly", explain);
  }, []);
  if (!DEMO_MODE) return null;
  const en = lang === "en";
  const title = en ? "Read-only demo" : "只读演示";
  return <Modal open={open} onClose={close} title={title} footer={<Button type="button" onClick={close}>{en ? "Got it" : "知道了"}</Button>}>
    <div role="alertdialog" aria-label={title}>
      {en ? "You can browse example's saved data. AI requests and creating, editing or deleting data are unavailable on this demo site." : "这里可以浏览 example 账户已保存的演示数据。演示站不支持 AI 调用，也不能新建、编辑或删除数据。"}
    </div>
  </Modal>;
}
