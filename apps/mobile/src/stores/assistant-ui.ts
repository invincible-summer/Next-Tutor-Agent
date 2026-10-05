import { create } from "zustand";
import { registerSessionCleanup } from "@/lib/session-lifecycle";
export const useAssistantUi = create<{
  open: boolean;
  setOpen: (open: boolean) => void;
}>((set) => ({ open: false, setOpen: (open) => set({ open }) }));
registerSessionCleanup(() => {
  useAssistantUi.setState({ open: false });
});
