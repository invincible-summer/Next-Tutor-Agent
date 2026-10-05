/**
 * Learning plan domain (mirrors `app/api/v1/orchestration.py`): goals,
 * weekly plans, daily tasks and SRS review. Payloads follow the
 * orchestration store's dict projections; responses are loose shapes with
 * the stable fields typed.
 */
import type { AbortSignalLike, Transport } from "./types.ts";

export interface LearningGoal {
  id: string;
  title: string;
  description?: string;
  goal_type?: string;
  subjects?: string[];
  target_concept_ids?: string[];
  workspace_id?: string;
  deadline?: number;
  created_at?: number;
  updated_at?: number;
  [key: string]: unknown;
}

export interface GoalState {
  goal_id: string;
  goal_title: string;
  subject?: string;
  supported_ratio?: number;
  total_skills?: number;
  supported_skills?: number;
  gaps?: Record<string, unknown>[];
  recommended_strategy?: string;
  urgency?: string;
  [key: string]: unknown;
}

export interface PlanConcept {
  concept_id: string;
  name: string;
  milestone_id?: string;
  week_index?: number;
  difficulty?: number;
}

export interface SubTask {
  id: string;
  title: string;
  source?: string;
  estimate_minutes?: number;
  done: boolean;
  done_at?: number | null;
}

export interface WeekTask {
  id: string;
  title: string;
  concept_ids?: string[];
  kind?: string;
  source?: "auto" | "user";
  done: boolean;
  workspace_id?: string;
  subtasks?: SubTask[];
}

export interface WeeklyPlan {
  week_index: number;
  week_start?: number | null;
  focus?: string;
  origin?: "auto" | "user";
  concepts?: PlanConcept[];
  tasks?: WeekTask[];
}

export interface DailyTask {
  id: string;
  day: string;
  title?: string;
  concept_id?: string;
  concept_name?: string;
  kind?: string;
  status?: "pending" | "in_progress" | "completed";
  priority?: number;
  estimate_minutes?: number;
  milestone_id?: string;
  week_task_id?: string;
  workspace_id?: string;
  phase?: string;
  custom?: boolean;
  reason?: string;
  episode_id?: string;
  session_id?: string;
  created_at?: number;
  completed_at?: number | null;
  [key: string]: unknown;
}

export interface HabitStats {
  current_streak?: number;
  longest_streak?: number;
  last_active_day?: string | null;
  total_active_days?: number;
  completed_tasks?: number;
  total_tasks?: number;
  completion_rate?: number;
  [key: string]: unknown;
}

export interface ReviewItem {
  concept_id: string;
  concept_name?: string;
  workspace_id?: string;
  easiness?: number;
  interval?: number;
  repetitions?: number;
  next_review?: number | string | null;
  [key: string]: unknown;
}

export interface LearningPlanSummary {
  student_id?: string;
  goals?: LearningGoal[];
  goal_states?: GoalState[];
  milestones?: Record<string, unknown>[];
  weekly_plan?: WeeklyPlan[];
  daily_tasks?: DailyTask[];
  schedule?: Record<string, unknown>;
  habit?: HabitStats;
  review_queue?: Record<string, ReviewItem>;
  srs_due_count?: number;
  pending_today?: number;
  needs_replan?: boolean;
  capacity?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface GoalPayload {
  title: string;
  description?: string;
  goal_type?: string;
  subjects?: string[];
  target_concept_ids?: string[];
  workspace_id?: string;
  deadline?: number;
}

export type GoalPatch = Partial<GoalPayload>;

export interface TaskCreatePayload {
  /** ISO date `YYYY-MM-DD`. */
  day?: string;
  title?: string;
  concept_id?: string;
  concept_name?: string;
  kind?: string;
  phase?: string;
  estimate_minutes?: number;
  priority?: number;
  milestone_id?: string;
}

export type TaskPatchPayload = Partial<TaskCreatePayload> & { status?: string };

export interface WeekConceptPayload {
  concept_id?: string;
  name?: string;
  difficulty?: number;
  milestone_id?: string;
}

export interface WeekTaskPayload {
  title: string;
  concept_ids?: string[];
  kind?: string;
}

export interface SubTaskPayload {
  title: string;
  estimate_minutes?: number;
}

export interface LaunchResponse {
  ok?: boolean;
  task_id: string;
  episode_id?: string;
  session_id?: string;
  /** Server-relative chat deep link, e.g. `/chat/{session_id}`. */
  launch_url: string;
  resumed?: boolean;
}

export interface LearningClient {
  plan(signal?: AbortSignalLike | null): Promise<LearningPlanSummary>;
  today(signal?: AbortSignalLike | null): Promise<DailyTask[]>;
  habit(signal?: AbortSignalLike | null): Promise<HabitStats>;
  review(signal?: AbortSignalLike | null): Promise<ReviewItem[]>;
  setGoal(payload: GoalPayload): Promise<{ ok: boolean; goal_id: string; weeks: WeeklyPlan[]; first_task: DailyTask | null }>;
  patchGoal(goalId: string, patch: GoalPatch): Promise<{ ok: boolean; weeks: WeeklyPlan[]; first_task: DailyTask | null }>;
  deleteGoal(goalId: string): Promise<{ ok: boolean; weeks: WeeklyPlan[]; first_task: DailyTask | null }>;
  regenerate(numWeeks?: number): Promise<{ ok: boolean; reason: string; weeks: WeeklyPlan[] }>;
  addTask(payload: TaskCreatePayload): Promise<{ ok: boolean; task: DailyTask; capacity_warning?: Record<string, unknown> }>;
  updateTask(taskId: string, patch: TaskPatchPayload): Promise<{ ok: boolean; capacity_warning?: Record<string, unknown> }>;
  deleteTask(taskId: string): Promise<{ ok: boolean }>;
  completeTask(taskId: string): Promise<{ ok: boolean; emitted_events: number }>;
  /** Starts (or resumes) the task's chat episode; returns the deep link. */
  launchTask(taskId: string): Promise<LaunchResponse>;
  patchSchedule(dailyMinutes: number): Promise<{ ok: boolean; schedule: Record<string, unknown>; capacity: Record<string, unknown> }>;
  addWeek(payload: { focus?: string; concepts?: WeekConceptPayload[]; week_start?: number | null }): Promise<{ ok: boolean; week: WeeklyPlan }>;
  deleteWeek(weekIndex: number): Promise<{ ok: boolean }>;
  addWeekConcept(weekIndex: number, payload: WeekConceptPayload): Promise<{ ok: boolean; concept: PlanConcept }>;
  removeWeekConcept(weekIndex: number, conceptId: string): Promise<{ ok: boolean }>;
  addWeekTask(weekIndex: number, payload: WeekTaskPayload): Promise<{ ok: boolean; task: WeekTask }>;
  deleteWeekTask(weekIndex: number, taskId: string): Promise<{ ok: boolean }>;
  addSubtask(weekIndex: number, taskId: string, payload: SubTaskPayload): Promise<{ ok: boolean; subtask: SubTask }>;
  toggleSubtask(weekIndex: number, taskId: string, subtaskId: string): Promise<{ ok: boolean }>;
  deleteSubtask(weekIndex: number, taskId: string, subtaskId: string): Promise<{ ok: boolean }>;
  suggestSubtasks(weekIndex: number, taskId: string): Promise<{ ok: boolean; task: WeekTask }>;
}

export function createLearningClient(transport: Transport): LearningClient {
  const weekBase = (weekIndex: number) => `/orchestration/week/${encodeURIComponent(String(weekIndex))}`;
  const weekTaskBase = (weekIndex: number, taskId: string) =>
    `${weekBase(weekIndex)}/task/${encodeURIComponent(taskId)}`;
  const post = <T>(path: string, json?: unknown) =>
    transport.request<T>(path, { method: "POST", json }).then((result) => result.body);
  type GoalMutation = { ok: boolean; weeks: WeeklyPlan[]; first_task: DailyTask | null };  return {
    plan: (signal) =>
      transport.request<LearningPlanSummary>("/orchestration/plan", { signal })
        .then((result) => result.body),
    today: (signal) =>
      transport.request<DailyTask[]>("/orchestration/today", { signal })
        .then((result) => result.body),
    habit: (signal) =>
      transport.request<HabitStats>("/orchestration/habit", { signal })
        .then((result) => result.body),
    review: (signal) =>
      transport.request<ReviewItem[]>("/orchestration/review", { signal })
        .then((result) => result.body),
    setGoal: (payload) =>
      post<{ ok: boolean; goal_id: string; weeks: WeeklyPlan[]; first_task: DailyTask | null }>(
        "/orchestration/goal",
        {
          title: payload.title,
          description: payload.description ?? "",
          goal_type: payload.goal_type ?? "ability",
          subjects: payload.subjects ?? [],
          target_concept_ids: payload.target_concept_ids ?? [],
          workspace_id: payload.workspace_id ?? "",
          deadline: payload.deadline ?? 0,
        },
      ),
    patchGoal: (goalId, patch) =>
      transport
        .request<GoalMutation>(`/orchestration/goal/${encodeURIComponent(goalId)}`, {
          method: "PATCH",
          json: patch,
        })
        .then((result) => result.body),
    deleteGoal: (goalId) =>
      transport
        .request<GoalMutation>(`/orchestration/goal/${encodeURIComponent(goalId)}`, { method: "DELETE" })
        .then((result) => result.body),
    regenerate: (numWeeks) =>
      transport
        .request<{ ok: boolean; reason: string; weeks: WeeklyPlan[] }>("/orchestration/regenerate", {
          method: "POST",
          query: numWeeks ? { num_weeks: numWeeks } : undefined,
        })
        .then((result) => result.body),
    addTask: (payload) =>
      post<{ ok: boolean; task: DailyTask; capacity_warning?: Record<string, unknown> }>(
        "/orchestration/task",
        payload,
      ),
    updateTask: (taskId, patch) =>
      transport
        .request<{ ok: boolean; capacity_warning?: Record<string, unknown> }>(
          `/orchestration/task/${encodeURIComponent(taskId)}`,
          { method: "PATCH", json: patch },
        )
        .then((result) => result.body),
    deleteTask: (taskId) =>
      transport
        .request<{ ok: boolean }>(`/orchestration/task/${encodeURIComponent(taskId)}`, { method: "DELETE" })
        .then((result) => result.body),
    completeTask: (taskId) =>
      post<{ ok: boolean; emitted_events: number }>(
        `/orchestration/task/${encodeURIComponent(taskId)}/complete`,
        {},
      ),
    launchTask: (taskId) =>
      post<LaunchResponse>(`/orchestration/task/${encodeURIComponent(taskId)}/launch`, {}),
    patchSchedule: (dailyMinutes) =>
      transport
        .request<{ ok: boolean; schedule: Record<string, unknown>; capacity: Record<string, unknown> }>(
          "/orchestration/schedule",
          {
            method: "PATCH",
            json: { daily_minutes: dailyMinutes },
          },
        )
        .then((result) => result.body),
    addWeek: (payload) =>
      post<{ ok: boolean; week: WeeklyPlan }>("/orchestration/week", payload),
    deleteWeek: (weekIndex) =>
      transport
        .request<{ ok: boolean }>(weekBase(weekIndex), { method: "DELETE" })
        .then((result) => result.body),
    addWeekConcept: (weekIndex, payload) =>
      post<{ ok: boolean; concept: PlanConcept }>(`${weekBase(weekIndex)}/concept`, payload),
    removeWeekConcept: (weekIndex, conceptId) =>
      transport
        .request<{ ok: boolean }>(`${weekBase(weekIndex)}/concept/${encodeURIComponent(conceptId)}`, {
          method: "DELETE",
        })
        .then((result) => result.body),
    addWeekTask: (weekIndex, payload) =>
      post<{ ok: boolean; task: WeekTask }>(`${weekBase(weekIndex)}/task`, payload),
    deleteWeekTask: (weekIndex, taskId) =>
      transport
        .request<{ ok: boolean }>(weekTaskBase(weekIndex, taskId), { method: "DELETE" })
        .then((result) => result.body),
    addSubtask: (weekIndex, taskId, payload) =>
      post<{ ok: boolean; subtask: SubTask }>(`${weekTaskBase(weekIndex, taskId)}/subtask`, payload),
    toggleSubtask: (weekIndex, taskId, subtaskId) =>
      transport
        .request<{ ok: boolean }>(
          `${weekTaskBase(weekIndex, taskId)}/subtask/${encodeURIComponent(subtaskId)}`,
          { method: "PATCH" },
        )
        .then((result) => result.body),
    deleteSubtask: (weekIndex, taskId, subtaskId) =>
      transport
        .request<{ ok: boolean }>(
          `${weekTaskBase(weekIndex, taskId)}/subtask/${encodeURIComponent(subtaskId)}`,
          { method: "DELETE" },
        )
        .then((result) => result.body),
    suggestSubtasks: (weekIndex, taskId) =>
      post<{ ok: boolean; task: WeekTask }>(`${weekTaskBase(weekIndex, taskId)}/suggest`, {}),
  };
}
