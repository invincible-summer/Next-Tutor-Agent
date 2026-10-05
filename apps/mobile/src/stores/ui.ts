import { create } from "zustand";

import { loadPref, PREF_KEYS, savePref } from "@/platform/prefs";
import { AUTO_GRADE, GRADES, type Grade } from "@/lib/grade";

export type OutputLanguage = "auto" | "zh" | "en";

interface UiPrefsState {
  /** 当前学段胶囊（会话内可覆盖默认；持久化为默认学段）。 */
  grade: Grade;
  defaultGrade: Grade;
  outputLanguage: OutputLanguage;
  hydrated: boolean;
  hydrate: () => Promise<void>;
  setGrade: (grade: Grade) => void;
  setDefaultGrade: (grade: Grade) => void;
  setOutputLanguage: (lang: OutputLanguage) => void;
}

function asGrade(value: string | null): Grade {
  return value && (GRADES as string[]).includes(value)
    ? (value as Grade)
    : AUTO_GRADE;
}

function asOutputLanguage(value: string | null): OutputLanguage {
  return value === "zh" || value === "en" ? value : "auto";
}

/** 全局 UI 偏好（学段/回答语言）：AsyncStorage 持久化，非敏感。 */
export const useUiPrefs = create<UiPrefsState>((set) => ({
  grade: AUTO_GRADE,
  defaultGrade: AUTO_GRADE,
  outputLanguage: "auto",
  hydrated: false,
  hydrate: async () => {
    const [grade, outputLang] = await Promise.all([
      loadPref(PREF_KEYS.grade),
      loadPref(PREF_KEYS.outputLang),
    ]);
    const resolved = asGrade(grade);
    set({
      defaultGrade: resolved,
      grade: resolved,
      outputLanguage: asOutputLanguage(outputLang),
      hydrated: true,
    });
  },
  setGrade: (grade) => set({ grade }),
  setDefaultGrade: (grade) => {
    set({ defaultGrade: grade, grade });
    void savePref(PREF_KEYS.grade, grade);
  },
  setOutputLanguage: (outputLanguage) => {
    set({ outputLanguage });
    void savePref(PREF_KEYS.outputLang, outputLanguage);
  },
}));
