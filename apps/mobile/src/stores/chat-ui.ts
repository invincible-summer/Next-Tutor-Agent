import { create } from "zustand";
import { registerSessionCleanup } from "@/lib/session-lifecycle";
export const useChatUi = create<{
  quote: string;
  setQuote: (quote: string) => void;
}>((set) => ({ quote: "", setQuote: (quote) => set({ quote }) }));
registerSessionCleanup(() => {
  useChatUi.setState({ quote: "" });
});
