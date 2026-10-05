import React from "react";
import { fireEvent, render } from "@testing-library/react-native";
import type { QuestionPublic } from "@next-tutor/api-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "@/ui/ThemeProvider";
import { PublicQuestion } from "@/features/assessment/PublicQuestion";

let mockFigure: unknown = null;
const clients = new Set<QueryClient>();
const mockRetry = jest.fn(async () => ({}));
const mockStart = jest.fn(async () => ({ status: "queued" }));
jest.mock("@/lib/copy", () => ({ useCopy: () => (zh: string) => zh }));
jest.mock("expo-router", () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn() }),
}));
jest.mock("@/providers/AuthProvider", () => ({
  useAuth: () => ({ owner: "synthetic-tenant:synthetic-user" }),
}));
jest.mock("@/lib/api", () => ({
  apiClient: () => ({ illustration: { retry: mockRetry, start: mockStart } }),
}));
jest.mock("@/lib/server-state", () => ({
  useServerQuery: () => ({ data: mockFigure }),
  useAction: () => ({
    pending: false,
    run: async (
      work: () => Promise<unknown>,
      success?: (value: unknown) => void,
    ) => {
      const value = await work();
      success?.(value);
      return value;
    },
  }),
  record: (value: unknown) => (value && typeof value === "object" ? value : {}),
  str: (value: unknown) => (typeof value === "string" ? value : ""),
}));
jest.mock("@/ui/SvgCanvas", () => ({ SvgCanvas: () => null }));

const base: QuestionPublic = {
  question_id: "synthetic-question-1",
  question_revision: 1,
  q_type: "short_answer",
  stem: "项目自编合成题：请说明两个数相加的过程。",
  options: {},
  input_spec: { kind: "text", max_bytes: 20, requires_explanation: false },
  concept_refs: [],
  source_badge: "Synthetic fixture",
  hints_available: false,
  illustration: null,
  visual_role: "essential",
};
async function question(
  props: Partial<React.ComponentProps<typeof PublicQuestion>> = {},
) {
  const onSubmit = jest.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  clients.add(client);
  const ui = await render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <PublicQuestion question={base} onSubmit={onSubmit} {...props} />
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return { ...ui, onSubmit };
}

describe("public assessment question", () => {
  afterEach(() => {
    for (const client of clients) client.clear();
    clients.clear();
  });
  beforeEach(() => {
    mockFigure = null;
    mockRetry.mockClear();
    mockStart.mockClear();
  });

  test("essential missing image blocks both editing and submission", async () => {
    const ui = await question();
    expect(ui.getByTestId("question-answer").props.editable).toBe(false);
    expect(ui.getByTestId("question-submit")).toBeDisabled();
    await fireEvent.press(ui.getByTestId("question-submit"));
    expect(ui.onSubmit).not.toHaveBeenCalled();
  });

  test("supplemental missing image does not prevent a text answer", async () => {
    const ui = await question({
      question: { ...base, visual_role: "supplemental" },
    });
    expect(ui.getByTestId("question-answer").props.editable).toBe(true);
    await fireEvent.changeText(ui.getByTestId("question-answer"), "合成回答");
    expect(ui.getByTestId("question-submit")).toBeEnabled();
    await fireEvent.press(ui.getByTestId("question-submit"));
    expect(ui.onSubmit).toHaveBeenCalledWith("合成回答");
  });

  test("UTF-8 byte budget prevents an oversized answer", async () => {
    const ui = await question({ question: { ...base, visual_role: "none" } });
    await fireEvent.changeText(
      ui.getByTestId("question-answer"),
      "中文中文中文中文",
    );
    expect(ui.getByTestId("question-submit")).toBeDisabled();
  });

  test("stopped assessment cannot trigger image retry or submit", async () => {
    mockFigure = {
      status: "failed",
      failure: { retryable: true },
      job_id: "synthetic-job-1",
    };
    const ui = await question({ active: false });
    expect(ui.queryByText("重试题图")).toBeNull();
    expect(ui.getByTestId("question-submit")).toBeDisabled();
    expect(mockRetry).not.toHaveBeenCalled();
  });

  test("active retry button uses the server-owned failed image job", async () => {
    mockFigure = {
      status: "failed",
      failure: { retryable: true },
      job_id: "synthetic-job-1",
    };
    const ui = await question();
    await fireEvent.press(ui.getByText("重试题图"));
    expect(mockRetry).toHaveBeenCalledWith("synthetic-job-1");
  });

  test("stopped assessment cannot initiate supplemental image generation", async () => {
    mockFigure = { status: "not_required" };
    await question({ active: false, allowEnrichment: true });
    expect(mockStart).not.toHaveBeenCalled();
  });
});
