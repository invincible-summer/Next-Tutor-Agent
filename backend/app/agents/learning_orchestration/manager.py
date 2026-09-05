"""LearningOrchestrationService: the single facade for M9.

Like UXService (M8), EvaluationService (M7), MemoryService (M6),
KnowledgeService (M5), and AssessmentManager (M4), this is the one entry point
the rest of the app uses. It exposes:

    lo = get_orchestration_service()
    lo.build_directive(...)      # READ: JIT analysis -> "[编排智能·...]"
    lo.record_turn(...)          # WRITE: capture SRS + habit + milestone updates
                                 #        (+ auto-advance today's tasks, 6g)
    lo.add_goal(...)             # WRITE: append a long-term goal (multi-goal)
    lo.update_goal(...)          # WRITE: patch one goal (SRS/tasks preserved)
    lo.delete_goal(...)          # WRITE: remove one goal + its gap analysis
    await lo.plan_milestones(...)  # WRITE: LLM milestone decomposition (+ fallback)
    lo.regenerate_plan(...)      # WRITE: re-plan from current state
    await lo.today_tasks(...)    # READ: today's tasks + carryover (LLM compose)
    lo.complete_task(...)        # WRITE: mark a task done
    lo.add_task(...) / lo.update_task(...) / lo.delete_task(...)  # task CRUD
    lo.summary(...)              # READ: full state for the API (+ needs_replan)

Design contract (mirrors M2/M3/M4/M5/M6/M7/M8):
  - OBSERVER + EVENT EMITTER: owns ONLY orchestration state (plans,
    schedules, SRS cards, task execution). Reads M2 mastery, M3 curriculum, M5
    graph, M6 episodes read-only. It does NOT directly write M2/M3/M5/M6
    storage -- BUT it emits OrchestrationLearningEvents (milestone reached,
    streak achieved, goal-progress) which the supervisor forwards into M6's
    event bus (M6 decides whether to persist them). See event_emitter.py.
  - REUSE NOT REBUILD: learning_planner reuses M3's build_learning_path as its
    inference engine; never rebuilds learning-path logic.
  - ORTHOGONAL SRS: SM-2 scheduling state is M9's; mastery posterior is M2's.
  - GRACEFUL: any failure degrades to a no-op; never breaks a turn. Toggled by
    ORCHESTRATION_MODE (default on). When off, both supervisor hooks are
    no-ops and M1-M8 behavior is byte-identical.
  - DETERMINISTIC-FIRST: SM-2, habit, schedule, planning are all pure
    functions (zero LLM). No LLM on the per-turn critical path. LLM calls
    (milestone decomposition, daily task composition) happen ONLY in
    API-initiated async paths, each gated + deterministically backstopped.
  - TASK UNIQUENESS: a persisted task's identity is stable. Re-composition
    (next day / regenerate / LLM composer) only gap-fills missing
    (concept_id, kind) keys via task_executor.materialize_day -- it never
    replaces or deletes existing tasks; user-created tasks (custom=True) are
    never touched by any pipeline; unfinished tasks carry over as overdue.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from . import (context_builder, daily_composer, event_emitter, goal_analyzer,
               goal_manager, habit_tracker, learning_planner,
               schedule_engine,
               spaced_repetition as srs, store, subtask_advisor, task_executor,
               weekly_planner_llm)
from .schema import (DailyTask, DailyTaskStatus, GoalState, LearningGoal,
                     OrchestrationEvent,
                     OrchestrationLearningEvent, OrchestrationState, PlanConcept,
                     ReviewItem, SubTask, TASK_PHASES,
                     TaskKind,
                     WeekTask, WeeklyPlan, _MAX_SUBTASKS,
                     _MAX_TASKS_PER_DAY,
                     _MAX_WEEK_TASKS)

_WEEK_SECONDS = 7 * 24 * 3600


def is_enabled() -> bool:
    """Whether the Learning Orchestration layer is active (default on)."""
    return os.getenv("ORCHESTRATION_MODE", "1") not in (
        "0", "false", "False", "off")


class LearningOrchestrationService:
    """Facade over goal/milestone/plan/SRS/habit/schedule.

    Stateless; all persistence is file-backed per-student. A single shared
    instance is cached per process.
    """
    _instance: "LearningOrchestrationService | None" = None

    @classmethod
    def get(cls) -> "LearningOrchestrationService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # --- internal helpers ------------------------------------------------

    def _load(self, student_id: str) -> OrchestrationState:
        return store.load_state(student_id)

    def _save(self, student_id: str, state: OrchestrationState,
              *, event: OrchestrationEvent | None = None) -> bool:
        ok = store.save_state(student_id, state)
        if event:
            store.append_event(student_id, event)
        return ok

    # --- READ SIDE: JIT analysis -> directive string --------------------

    def build_directive(self, *, student_id: str, concept: str = "",
                        subject: str = "", intent: str = "",
                        now: float | None = None) -> str:
        """Build the [编排智能·...] advisory block for this turn.

        Returns "" when there is nothing actionable. This is the single call
        the Supervisor makes per turn (step 3h). Never raises.
        """
        try:
            state = self._load(student_id)
            return context_builder.build_orchestration_directive(
                state, concept=concept, subject=subject, intent=intent, now=now)
        except Exception:
            return ""

    # --- WRITE SIDE: capture a turn's orchestration signals -------------

    def record_turn(self, *, student_id: str, session_id: str = "",
                    concept: str = "", subject: str = "",
                    user_message: str = "", answer: str = "",
                    intent: str = "", verdict: str = "",
                    now: float | None = None) -> list[OrchestrationLearningEvent]:
        """Capture one completed turn's orchestration signals.

        Updates the SRS review queue (if a concept was taught/assessed) and
        habit stats (read-only over the unified activity day-union), and emits
        OrchestrationLearningEvents for any streak-threshold or goal-progress
        checkpoint crossing detected. Those events are RETURNED so the
        supervisor can forward them into M6's event bus (M6 decides whether
        to persist them). M9 itself never writes M2/M3/M5/M6 storage. Never
        raises; returns [] on failure.
        """
        emitted: list[OrchestrationLearningEvent] = []
        try:
            now = now if now is not None else time.time()
            state = self._load(student_id)
            changed = False

            # SRS: if a concept was touched, create/update its review card.
            # A07（updatePlan.md）：exposure（只听讲、无作答判定）与 unknown
            # 判定都只是「接触」——登记/建卡、安排首次检查，但不喂给 SM-2
            # 通过路径，否则听两轮课也会被当成两次成功召回，复习间隔被
            # 无证据拉长。只有有效 recall 判定才更新复习质量。
            if concept:
                cid = concept.strip()
                card = state.review_queue.get(cid)
                quality = srs.quality_from_verdict(verdict) if verdict else None
                if quality is None:
                    if card is None:
                        card = srs.create_card(cid, concept_name=cid, now=now)
                        state.review_queue[cid] = card
                        changed = True
                else:
                    if card is None:
                        card = srs.create_card(cid, concept_name=cid, now=now)
                    card = srs.update_review(card, quality, now=now)
                    state.review_queue[cid] = card
                    changed = True

            # habit refresh (read-only over the unified activity day-union,
            # the same L1 source M8 reads) + streak-threshold detect
            habit_tracker.refresh_habit(state, now=now, student_id=student_id)
            changed = True
            # detect a new streak threshold crossing
            subj = state.primary_subject or subject
            streak_ev = event_emitter.emit_for_streak(
                state.habit.current_streak,
                last_reported=state.last_streak_reported, subject=subj)
            if streak_ev:
                emitted.extend(streak_ev)
                # advance the dedup cursor to the highest threshold <= streak
                state.last_streak_reported = max(
                    t for t in event_emitter._STREAK_THRESHOLDS
                    if t <= state.habit.current_streak) if state.habit.current_streak >= 3                         else state.last_streak_reported

            # goal-progress checkpoint detect (read-only over M2 mastery)
            mastery_view = self._mastery_view_safe(student_id)
            if mastery_view and state.has_goals:
                from . import goal_manager as _gm
                ratio = _gm.overall_progress(state, mastery_view)
                prog_ev = event_emitter.emit_for_goal_progress(
                    ratio, last_reported=state.last_progress_reported,
                    subject=state.primary_subject or subject)
                if prog_ev:
                    emitted.extend(prog_ev)
                    state.last_progress_reported = max(
                        c for c in event_emitter._PROGRESS_CHECKPOINTS
                        if c <= ratio)

            # mark overdue tasks
            overdue = task_executor.mark_overdue(state, now=now)
            if overdue:
                changed = True

            # 6g auto-progress: learning behaviour advances today's tasks.
            # A concept-taught turn moves the matching today task to
            # in_progress; a quiz-verdict turn completes it (either verdict --
            # mastery itself is M2/SRS's job, not the task lifecycle's).
            if concept:
                auto_ev, auto_changed = self._auto_progress_tasks(
                    state, concept=cid, verdict=verdict, now=now)
                emitted.extend(auto_ev)
                changed = changed or auto_changed

            if changed or emitted:
                state.events_processed += 1
                self._save(student_id, state,
                           event=OrchestrationEvent(
                               type="turn_recorded",
                               payload={"concept": concept, "verdict": verdict,
                                        "emitted": len(emitted)}))
        except Exception:
            pass
        return event_emitter.valid_events(emitted)

    # --- WRITE SIDE: goal + plan management ------------------------------

    def add_goal(self, student_id: str, *, title: str, description: str = "",
                 goal_type: str = "ability", subjects: list[str] | None = None,
                 deadline: float = 0.0,
                 target_concept_ids: list[str] | None = None) -> LearningGoal:
        """Append a long-term learning goal (multi-goal, capped).

        After setting the goal intent, runs GoalAnalyzer (modification 3) to
        populate the goal's entry in goal_states -- the gap analysis +
        backward plan that tells the LearningPlanner *why* the plan looks the
        way it does. With target_concept_ids the analysis runs over the
        goal's prerequisite closure instead of a whole subject. The analysis
        is best-effort (read-only over M2 mastery + M5 graph); a missing
        graph degrades to an empty GoalState, never breaks the turn.
        Raises ValueError on empty title or cap overflow (API maps to 400).
        """
        state = self._load(student_id)
        goal = goal_manager.add_goal(state, title=title,
                                     description=description,
                                     goal_type=goal_type, subjects=subjects,
                                     deadline=deadline,
                                     target_concept_ids=target_concept_ids)
        # goal reasoning: gap analysis + backward plan (read-only M2/M5)
        self._analyze_goals_safe(state, student_id=student_id)
        self._save(student_id, state,
                   event=OrchestrationEvent(type="goal_set",
                       payload={"goal_id": goal.id, "title": goal.title,
                                "goal_type": goal_type,
                                "bound_concepts":
                                    len(goal.target_concept_ids)}))
        return goal

    def update_goal(self, student_id: str, goal_id: str, *,
                    title: str | None = None,
                    description: str | None = None,
                    goal_type: str | None = None,
                    subjects: list[str] | None = None,
                    deadline: float | None = None,
                    target_concept_ids: list[str] | None = None) -> bool:
        """Patch fields of one existing goal (all parameters optional).

        Re-runs the gap analysis afterwards. The SRS review queue and all
        historical daily tasks are preserved untouched -- only the goal intent
        and its derived goal_state change. Returns False when the id does not
        exist. Never raises.
        """
        try:
            state = self._load(student_id)
            goal = goal_manager.update_goal(
                state, goal_id, title=title, description=description,
                goal_type=goal_type, subjects=subjects,
                deadline=deadline, target_concept_ids=target_concept_ids)
            if goal is None:
                return False
            self._analyze_goals_safe(state, student_id=student_id)
            self._save(student_id, state,
                       event=OrchestrationEvent(type="goal_updated",
                           payload={"goal_id": goal.id,
                                    "title": goal.title}))
            return True
        except Exception:
            return False

    def delete_goal(self, student_id: str, goal_id: str) -> bool:
        """Remove one goal and its gap analysis.

        The SRS queue, daily tasks, and user-created plan entries are
        preserved; the caller re-plans so the goal's concepts leave the
        auto plan. Returns False when the id does not exist. Never raises.
        """
        try:
            state = self._load(student_id)
            if not goal_manager.remove_goal(state, goal_id):
                return False
            self._save(student_id, state,
                       event=OrchestrationEvent(type="goal_deleted",
                           payload={"goal_id": goal_id}))
            return True
        except Exception:
            return False

    async def regenerate_plan(self, student_id: str, *, num_weeks: int = 4,
                              now: float | None = None) -> tuple[bool, str]:
        """Regenerate the weekly plan from current goal + mastery + graph.

        LLM-first: one weekly_planner_llm call lays out N weeks of
        action-level WeekTasks + SubTasks over the topo-ordered
        required_skills; the validation gate (concept subset / no repeat /
        full coverage / caps) falls back to the deterministic
        learning_planner + derive_tasks_fallback on any failure. Only future
        / unmaterialized content is recomputed: persisted daily_tasks are
        never touched, origin=user weeks and source=user tasks/subtasks are
        merged back untouched (the human-override contract).

        Returns (ok, reason) with reason in {"" | "no_goal" | "empty_plan"}.
        Every attempt stamps state.last_plan_attempt so needs_replan can tell
        "never planned" apart from "planned but nothing to schedule". An
        empty result with a goal is a legitimate end state (all mastered /
        nothing schedulable): the stale weekly plan is cleared and ok=True --
        this breaks the banner/retry loop, it is not an error.
        """
        try:
            now = now if now is not None else time.time()
            state = self._load(student_id)
            if not state.has_goals:
                return False, "no_goal"

            weeks: list[WeeklyPlan] | None = None
            # LLM weekly planner (validated; deterministic fallback below).
            # Big syllabi (100+ required concepts) are planned in a near-term
            # WINDOW: the gate's full-coverage rule applies to the window,
            # not the whole gap list -- later replans schedule the rest.
            # Multi-goal: every goal's required chain is merged (goal order,
            # deduped) into one shared window.
            if is_enabled():
                try:
                    if not any(gs.required_skills
                               for gs in state.goal_states):
                        self._analyze_goals_safe(state, student_id=student_id)
                    required: list[str] = []
                    seen: set[str] = set()
                    for gs in state.goal_states:
                        for sid in (gs.required_skills or []):
                            if sid not in seen:
                                seen.add(sid)
                                required.append(sid)
                    window = required[:num_weeks * learning_planner._MAX_CONCEPTS_PER_WEEK]
                    if window:
                        mastery_view = self._mastery_view_safe(student_id)
                        # concept-bound goals span subjects: look names up
                        # across the whole graph, not just the first subject
                        name_subject = ("" if any(g.target_concept_ids
                                                  for g in state.goals)
                                        else state.primary_subject)
                        names = self._concept_names_safe(
                            name_subject, student_id=student_id)
                        content, _usage = await self._get_llm().complete(
                            weekly_planner_llm.build_weekly_prompt(
                                state.goals_label, window, names,
                                mastery_view, num_weeks,
                                state.schedule.daily_minutes),
                            max_tokens=3000, disable_thinking=True)
                        skeletons = weekly_planner_llm.parse_weekly_response(
                            content, window, num_weeks)
                        if skeletons:
                            weeks = weekly_planner_llm.weeks_from_skeletons(
                                skeletons, names, now=now)
                except Exception:
                    weeks = None

            if weeks is None:
                # deterministic fallback: graph topo-sort + schedule split
                inputs = self._assemble_plan_inputs(student_id, state, now)
                weeks = learning_planner.generate_weekly_plan(
                    state, next_learnable=inputs["next_learnable"],
                    review_candidates=inputs["review_candidates"],
                    mastery_view=inputs["mastery_view"],
                    prereq_map=inputs["prereq_map"],
                    num_weeks=num_weeks, now=now)
                weekly_planner_llm.derive_tasks_fallback(weeks)

            state.last_plan_attempt = now
            state.weekly_plan = _merge_user_plan(state.weekly_plan, weeks)
            self._save(student_id, state,
                       event=OrchestrationEvent(type="plan_regenerated",
                           payload={"weeks": len(state.weekly_plan)}))
            if state.weekly_plan:
                return True, ""
            return True, "empty_plan"
        except Exception:
            return False, ""

    # --- READ SIDE: API projections -------------------------------------

    async def today_tasks(self, student_id: str, *,
                          now: float | None = None,
                          compose_llm: bool = False) -> list[dict[str, Any]]:
        """Today's tasks plus the overdue carryover section (carryover first).

        Composes today's tasks when the day has none yet: by default the
        deterministic generator only (the read path must stay fast -- an
        inline LLM compose made the first dashboard visit of the day block
        for the whole model latency); ``compose_llm=True`` (explicit write
        actions: goal set / regenerate) additionally asks the LLM coach to
        pick from the deterministic pool. Persistence goes through
        materialize_day gap-fill, so existing tasks are never replaced.
        Never raises.
        """
        try:
            now = now if now is not None else time.time()
            state = await asyncio.to_thread(self._load, student_id)
            changed = await asyncio.to_thread(
                task_executor.mark_overdue, state, now=now) > 0
            changed = await self._compose_today_safe(
                state, now, student_id=student_id,
                use_llm=compose_llm) or changed
            if changed:
                await asyncio.to_thread(self._save, student_id, state)
            carryover = await asyncio.to_thread(
                task_executor.carryover_tasks, state, now=now)
            todays = await asyncio.to_thread(
                task_executor.today_tasks, state, now=now)
            return [t.to_dict() for t in carryover + todays]
        except Exception:
            return []

    async def _compose_today_safe(self, state: OrchestrationState,
                                  now: float, *, student_id: str = "",
                                  use_llm: bool = False) -> bool:
        """Compose today's tasks if the day is not yet composed. Returns True
        when state changed. A day with >= 1 non-custom task counts as already
        composed (idempotent). Never raises."""
        try:
            day = task_executor._day_str(now)
            if any(t.day == day and not t.custom for t in state.daily_tasks):
                return False
            slots = schedule_engine.slots_per_day(state.schedule, state.habit)
            tasks: list[DailyTask] = []
            pool: list[dict[str, Any]] = []
            if use_llm and is_enabled():
                # LLM coach: pick <= slots tasks from the deterministic pool.
                # The pool build reads the (cold-cacheable) student model, so
                # it runs off the event loop.
                try:
                    mastery_view = await asyncio.to_thread(
                        self._mastery_view_safe, student_id)
                    name_subject = ("" if any(g.target_concept_ids
                                              for g in state.goals)
                                    else state.primary_subject)
                    names = await asyncio.to_thread(
                        self._concept_names_safe, name_subject, student_id)
                    pool = await asyncio.to_thread(
                        daily_composer.build_candidate_pool, state,
                        mastery_view=mastery_view, concept_names=names, now=now)
                    if pool:
                        context = await asyncio.to_thread(
                            self._compose_context_safe, student_id)
                        content, _usage = await self._get_llm().complete(
                            daily_composer.build_compose_prompt(
                                pool, len(slots), goal_title=state.goals_label,
                                context=context),
                            max_tokens=1200, disable_thinking=True)
                        picks = daily_composer.parse_compose_response(
                            content, pool, len(slots))
                        if picks:
                            tasks = await asyncio.to_thread(
                                daily_composer.tasks_from_picks,
                                state, picks, pool, slots, now=now)
                except Exception:
                    tasks = []
            if not tasks and state.weekly_plan:
                # deterministic fallback: existing generator + template reasons
                def _fallback() -> list[DailyTask]:
                    return daily_composer.annotate_fallback(
                        task_executor.generate_daily_tasks(state, day_ts=now,
                                                           slots=slots))
                tasks = await asyncio.to_thread(_fallback)
            if tasks:
                task_executor.materialize_day(state, day, tasks)
                return True
            return False
        except Exception:
            return False

    def _auto_progress_tasks(self, state: OrchestrationState, *,
                             concept: str, verdict: str,
                             now: float) -> tuple[list[OrchestrationLearningEvent], bool]:
        """Advance today's tasks matching the turn's concept (6g enhancement).

        concept-taught turn -> pending task becomes in_progress; quiz-verdict
        turn -> pending/in_progress task becomes completed (regardless of the
        verdict). Emits the existing task_batch_completed event when that
        finishes all of today's tasks. Deterministic, no LLM. Never raises.
        """
        emitted: list[OrchestrationLearningEvent] = []
        try:
            cid = (concept or "").strip()
            if not cid:
                return emitted, False
            day = task_executor._day_str(now)
            todays = [t for t in state.daily_tasks if t.day == day]
            changed = False
            for t in todays:
                if t.concept_id != cid and t.concept_name != cid:
                    continue
                if verdict:
                    if t.status.value in ("pending", "in_progress"):
                        t.status = DailyTaskStatus.COMPLETED
                        t.completed_at = now
                        changed = True
                else:
                    if t.status.value == "pending":
                        t.status = DailyTaskStatus.IN_PROGRESS
                        changed = True
            if changed and verdict and todays and all(
                    t.status.value == "completed" for t in todays):
                subj = state.primary_subject
                emitted.append(event_emitter.task_batch_completed_event(
                    day, len(todays), subject=subj))
            return emitted, changed
        except Exception:
            return [], False


    def complete_task(self, student_id: str, task_id: str)             -> tuple[bool, list[OrchestrationLearningEvent]]:
        """Mark a daily task as completed.

        Returns (found, emitted_events). When the completion finishes ALL of
        today's tasks, emits a task_batch_completed event for the supervisor
        to forward into M6. Never raises.
        """
        emitted: list[OrchestrationLearningEvent] = []
        try:
            now = time.time()
            state = self._load(student_id)
            ok = task_executor.complete_task(state, task_id)
            if ok:
                # subtask write-back: a daily task materialised from a week
                # subtask completes that subtask (the plan hierarchy reacts
                # to what the student actually did). Title guard: ids are
                # positional (wt_{week}_{seq}) and can be reused by a later
                # regeneration for different content — only credit when the
                # referenced subtask is still the same work.
                done_task = next((t for t in state.daily_tasks
                                  if t.id == task_id), None)
                if done_task is not None and done_task.subtask_id:
                    for w in state.weekly_plan:
                        for wt in w.tasks:
                            if wt.id != done_task.week_task_id:
                                continue
                            for st in wt.subtasks:
                                if st.id == done_task.subtask_id and not st.done \
                                        and (not done_task.title
                                             or st.title == done_task.title):
                                    st.done = True
                                    st.done_at = now
                # detect batch completion: all of today's tasks done
                day = task_executor._day_str(now)
                todays = [t for t in state.daily_tasks if t.day == day]
                if todays and all(t.status.value == "completed" for t in todays):
                    subj = state.primary_subject
                    emitted.append(event_emitter.task_batch_completed_event(
                        day, len(todays), subject=subj))
                self._save(student_id, state,
                           event=OrchestrationEvent(type="task_completed",
                               payload={"task_id": task_id,
                                        "batch_complete": bool(emitted)}))
            return ok, event_emitter.valid_events(emitted)
        except Exception:
            return False, []

    # --- WRITE SIDE: user task CRUD --------------------------------------

    def add_task(self, student_id: str, *, day: str = "", title: str = "",
                 concept_id: str = "", concept_name: str = "",
                 kind: str = "study", phase: str = "",
                 estimate_minutes: int = 15, priority: int = 3,
                 milestone_id: str = "") -> DailyTask:
        """Create a user task (custom=True, id ``user_{day}_{seq}``).

        Raises ValueError on illegal kind/phase or when the per-day
        (_MAX_TASKS_PER_DAY) / total (140) caps are exceeded -- the API maps
        that to a 400. User tasks are never touched by any pipeline
        (materialize_day gap-fill, composer, regenerate).
        """
        if kind not in {k.value for k in TaskKind}:
            raise ValueError(f"illegal task kind: {kind}")
        if phase and phase not in TASK_PHASES:
            raise ValueError(f"illegal task phase: {phase}")
        state = self._load(student_id)
        day = (day or "").strip() or task_executor._day_str(time.time())
        day_tasks = [t for t in state.daily_tasks if t.day == day]
        if len(day_tasks) >= _MAX_TASKS_PER_DAY:
            raise ValueError(f"day {day} already has {_MAX_TASKS_PER_DAY} tasks")
        if len(state.daily_tasks) >= _MAX_TASKS_PER_DAY * 7:
            raise ValueError("total task cap (140) reached")
        prefix = f"user_{day}_"
        seq = 1 + max(
            (int(t.id[len(prefix):]) for t in state.daily_tasks
             if t.id.startswith(prefix) and t.id[len(prefix):].isdigit()),
            default=0)
        task = DailyTask(
            id=f"{prefix}{seq}", day=day, concept_id=concept_id.strip(),
            concept_name=concept_name.strip() or concept_id.strip(),
            kind=TaskKind.from_value(kind), status=DailyTaskStatus.PENDING,
            priority=max(1, min(5, int(priority))),
            estimate_minutes=max(1, int(estimate_minutes)),
            milestone_id=milestone_id.strip(), title=title.strip(),
            phase=phase, custom=True)
        state.daily_tasks.append(task)
        self._save(student_id, state,
                   event=OrchestrationEvent(type="task_added",
                       payload={"task_id": task.id, "custom": True}))
        return task

    def update_task(self, student_id: str, task_id: str, *,
                    title: str | None = None, day: str | None = None,
                    kind: str | None = None, phase: str | None = None,
                    estimate_minutes: int | None = None,
                    priority: int | None = None,
                    milestone_id: str | None = None,
                    status: str | None = None) -> bool:
        """Patch mutable fields of one daily task.

        Mutable: title, day, kind, phase, estimate_minutes, priority,
        milestone_id, status. status -> completed stamps completed_at;
        -> pending clears it. Raises ValueError on illegal kind/phase/status
        or when moving to a day that is already full (API maps to 400).
        Returns False when the task does not exist.
        """
        if kind is not None and kind not in {k.value for k in TaskKind}:
            raise ValueError(f"illegal task kind: {kind}")
        if phase is not None and phase and phase not in TASK_PHASES:
            raise ValueError(f"illegal task phase: {phase}")
        if status is not None and status not in {
                s.value for s in DailyTaskStatus}:
            raise ValueError(f"illegal task status: {status}")
        state = self._load(student_id)
        task = next((t for t in state.daily_tasks if t.id == task_id), None)
        if task is None:
            return False
        if day is not None and day != task.day:
            if sum(1 for t in state.daily_tasks
                   if t.day == day) >= _MAX_TASKS_PER_DAY:
                raise ValueError(f"day {day} already has {_MAX_TASKS_PER_DAY} tasks")
            task.day = day
        if title is not None:
            task.title = title.strip()
        if kind is not None:
            task.kind = TaskKind.from_value(kind)
        if phase is not None:
            task.phase = phase
        if estimate_minutes is not None:
            task.estimate_minutes = max(1, int(estimate_minutes))
        if priority is not None:
            task.priority = max(1, min(5, int(priority)))
        if milestone_id is not None:
            task.milestone_id = milestone_id.strip()
        if status is not None:
            task.status = DailyTaskStatus.from_value(status)
            if status == "completed":
                task.completed_at = time.time()
            elif status == "pending":
                task.completed_at = 0.0
        self._save(student_id, state,
                   event=OrchestrationEvent(type="task_updated",
                       payload={"task_id": task_id}))
        return True

    def delete_task(self, student_id: str, task_id: str) -> bool:
        """Delete one daily task by id. Returns False when not found."""
        try:
            state = self._load(student_id)
            before = len(state.daily_tasks)
            state.daily_tasks = [t for t in state.daily_tasks
                                 if t.id != task_id]
            if len(state.daily_tasks) == before:
                return False
            self._save(student_id, state,
                       event=OrchestrationEvent(type="task_deleted",
                           payload={"task_id": task_id}))
            return True
        except Exception:
            return False

    # --- plan hierarchy CRUD: weeks / week concepts / week tasks ----------

    def add_week(self, student_id: str, *, focus: str = "",
                 concepts: list[dict[str, Any]] | None = None,
                 week_start: float | None = None) -> WeeklyPlan:
        """Append a manual week to the weekly plan.

        week_index = max existing + 1; week_start defaults to last week + 7
        days (or now when there is no plan yet). Raises ValueError when the
        per-week concept cap is exceeded (API maps to 400).
        """
        state = self._load(student_id)
        raw = list(concepts or [])
        if len(raw) > learning_planner._MAX_CONCEPTS_PER_WEEK:
            raise ValueError(
                f"week concept cap ({learning_planner._MAX_CONCEPTS_PER_WEEK}) exceeded")
        week_index = max((w.week_index for w in state.weekly_plan),
                         default=-1) + 1
        if week_start is None:
            last = max((w.week_start for w in state.weekly_plan), default=0.0)
            week_start = (last + _WEEK_SECONDS) if last > 0 else time.time()
        seen: set[str] = set()
        plan_concepts: list[PlanConcept] = []
        for c in raw:
            cid = str(c.get("concept_id", "")).strip()
            key = cid or str(c.get("name", "")).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            plan_concepts.append(PlanConcept(
                concept_id=cid, name=str(c.get("name", "")).strip() or cid,
                milestone_id=str(c.get("milestone_id", "")).strip(),
                week_index=week_index,
                difficulty=max(1, min(5, int(c.get("difficulty", 3))))))
        week = WeeklyPlan(week_index=week_index, week_start=float(week_start),
                          focus=focus.strip()
                          or (plan_concepts[0].name if plan_concepts else ""),
                          concepts=plan_concepts, origin="user")
        state.weekly_plan.append(week)
        self._save(student_id, state,
                   event=OrchestrationEvent(type="week_added",
                       payload={"week_index": week_index}))
        return week

    def delete_week(self, student_id: str, week_index: int) -> bool:
        """Delete one week from the weekly plan. Materialized daily tasks are
        never touched (uniqueness contract). Returns False when not found."""
        try:
            state = self._load(student_id)
            before = len(state.weekly_plan)
            state.weekly_plan = [w for w in state.weekly_plan
                                 if w.week_index != week_index]
            if len(state.weekly_plan) == before:
                return False
            self._save(student_id, state,
                       event=OrchestrationEvent(type="week_deleted",
                           payload={"week_index": week_index}))
            return True
        except Exception:
            return False

    def add_week_concept(self, student_id: str, week_index: int, *,
                         concept_id: str = "", name: str = "",
                         difficulty: int = 3,
                         milestone_id: str = "") -> PlanConcept | None:
        """Add one concept to a week. Free-text concepts (concept_id="") are
        allowed. Raises ValueError on duplicate (same concept_id already in
        the week) or per-week cap overflow (API maps to 400). Returns None
        when the week does not exist (API maps to 404).
        """
        state = self._load(student_id)
        week = next((w for w in state.weekly_plan
                     if w.week_index == week_index), None)
        if week is None:
            return None
        cid = concept_id.strip()
        if cid and any(c.concept_id == cid for c in week.concepts):
            raise ValueError(f"concept {cid} already in week {week_index}")
        if len(week.concepts) >= learning_planner._MAX_CONCEPTS_PER_WEEK:
            raise ValueError(
                f"week concept cap ({learning_planner._MAX_CONCEPTS_PER_WEEK}) exceeded")
        pc = PlanConcept(concept_id=cid, name=name.strip() or cid,
                         milestone_id=milestone_id.strip(),
                         week_index=week_index,
                         difficulty=max(1, min(5, int(difficulty))))
        week.concepts.append(pc)
        if not week.focus:
            week.focus = pc.name
        self._save(student_id, state,
                   event=OrchestrationEvent(type="week_concept_added",
                       payload={"week_index": week_index, "concept_id": cid}))
        return pc

    def remove_week_concept(self, student_id: str, week_index: int,
                            concept_id: str) -> bool:
        """Remove one concept from a week (matches by concept_id, or by name
        for free-text concepts). Daily tasks are never touched. Returns False
        when the week or concept is not found."""
        try:
            state = self._load(student_id)
            week = next((w for w in state.weekly_plan
                         if w.week_index == week_index), None)
            if week is None:
                return False
            before = len(week.concepts)
            week.concepts = [c for c in week.concepts
                             if not (c.concept_id == concept_id
                                     or (not c.concept_id
                                         and c.name == concept_id))]
            if len(week.concepts) == before:
                return False
            self._save(student_id, state,
                       event=OrchestrationEvent(type="week_concept_removed",
                           payload={"week_index": week_index,
                                    "concept_id": concept_id}))
            return True
        except Exception:
            return False

    # --- week tasks + subtasks CRUD ----------------------------------------

    @staticmethod
    def _find_week(state: OrchestrationState,
                   week_index: int) -> WeeklyPlan | None:
        return next((w for w in state.weekly_plan
                     if w.week_index == week_index), None)

    @staticmethod
    def _find_week_task(week: WeeklyPlan, task_id: str) -> WeekTask | None:
        return next((t for t in week.tasks if t.id == task_id), None)

    def add_week_task(self, student_id: str, week_index: int, *, title: str,
                      concept_ids: list[str] | None = None,
                      kind: str = "study") -> WeekTask | None:
        """Create a user week task (id ``user_wt_{week}_{seq}``, source=user).

        User week tasks survive regeneration (merged back onto the rebuilt
        auto week by _merge_user_plan). Raises ValueError on empty title,
        illegal kind, or cap overflow (API maps to 400). Returns None when
        the week does not exist (API maps to 404).
        """
        title = (title or "").strip()
        if not title:
            raise ValueError("week-task title required")
        if kind not in {k.value for k in TaskKind}:
            raise ValueError(f"illegal task kind: {kind}")
        state = self._load(student_id)
        week = self._find_week(state, week_index)
        if week is None:
            return None
        if len(week.tasks) >= _MAX_WEEK_TASKS:
            raise ValueError(f"week task cap ({_MAX_WEEK_TASKS}) reached")
        prefix = f"user_wt_{week_index}_"
        seq = 1 + max(
            (int(t.id[len(prefix):]) for t in week.tasks
             if t.id.startswith(prefix) and t.id[len(prefix):].isdigit()),
            default=0)
        wt = WeekTask(id=f"{prefix}{seq}", title=title,
                      concept_ids=list(dict.fromkeys(concept_ids or [])),
                      kind=kind, source="user")
        week.tasks.append(wt)
        self._save(student_id, state,
                   event=OrchestrationEvent(type="week_task_added",
                       payload={"week_index": week_index, "task_id": wt.id}))
        return wt

    def delete_week_task(self, student_id: str, week_index: int,
                         task_id: str) -> bool:
        """Delete one week task. Returns False when week/task not found."""
        try:
            state = self._load(student_id)
            week = self._find_week(state, week_index)
            if week is None:
                return False
            before = len(week.tasks)
            week.tasks = [t for t in week.tasks if t.id != task_id]
            if len(week.tasks) == before:
                return False
            self._save(student_id, state,
                       event=OrchestrationEvent(type="week_task_deleted",
                           payload={"week_index": week_index,
                                    "task_id": task_id}))
            return True
        except Exception:
            return False

    def add_subtask(self, student_id: str, week_index: int, task_id: str, *,
                    title: str, estimate_minutes: int = 15,
                    source: str = "user") -> SubTask | None:
        """Append a subtask to a week task (id ``{source}_st_{task}_{seq}``).

        Raises ValueError on empty title or cap overflow (API maps to 400).
        Returns None when the week or task does not exist (API maps to 404).
        """
        title = (title or "").strip()
        if not title:
            raise ValueError("subtask title required")
        state = self._load(student_id)
        week = self._find_week(state, week_index)
        task = self._find_week_task(week, task_id) if week else None
        if task is None:
            return None
        if len(task.subtasks) >= _MAX_SUBTASKS:
            raise ValueError(f"subtask cap ({_MAX_SUBTASKS}) reached")
        prefix = f"{source}_st_{task_id}_"
        seq = 1 + max(
            (int(s.id[len(prefix):]) for s in task.subtasks
             if s.id.startswith(prefix) and s.id[len(prefix):].isdigit()),
            default=0)
        st = SubTask(id=f"{prefix}{seq}", title=title, source=source,
                     estimate_minutes=max(1, int(estimate_minutes)))
        task.subtasks.append(st)
        self._save(student_id, state,
                   event=OrchestrationEvent(type="subtask_added",
                       payload={"week_index": week_index, "task_id": task_id,
                                "subtask_id": st.id, "source": source}))
        return st

    def toggle_subtask(self, student_id: str, week_index: int, task_id: str,
                       subtask_id: str) -> bool:
        """Flip one subtask's done flag (stamps done_at). False when missing."""
        try:
            state = self._load(student_id)
            week = self._find_week(state, week_index)
            task = self._find_week_task(week, task_id) if week else None
            st = next((s for s in (task.subtasks if task else [])
                       if s.id == subtask_id), None)
            if st is None:
                return False
            st.done = not st.done
            st.done_at = time.time() if st.done else 0.0
            self._save(student_id, state,
                       event=OrchestrationEvent(type="subtask_toggled",
                           payload={"subtask_id": subtask_id,
                                    "done": st.done}))
            return True
        except Exception:
            return False

    def delete_subtask(self, student_id: str, week_index: int, task_id: str,
                       subtask_id: str) -> bool:
        """Delete one subtask. Returns False when week/task/subtask missing."""
        try:
            state = self._load(student_id)
            week = self._find_week(state, week_index)
            task = self._find_week_task(week, task_id) if week else None
            if task is None:
                return False
            before = len(task.subtasks)
            task.subtasks = [s for s in task.subtasks if s.id != subtask_id]
            if len(task.subtasks) == before:
                return False
            self._save(student_id, state,
                       event=OrchestrationEvent(type="subtask_deleted",
                           payload={"subtask_id": subtask_id}))
            return True
        except Exception:
            return False

    async def suggest_subtasks(self, student_id: str, week_index: int,
                               task_id: str) -> WeekTask | None:
        """LLM-recommend subtasks for one week task and persist them.

        Generated subtasks are source="auto" (rebuilt on regeneration);
        titles already present are skipped. Returns the updated task, or
        None when the week/task does not exist OR generation failed (the API
        maps the latter to 502 so the UI can show a quiet failure note).
        Never raises.
        """
        try:
            state = self._load(student_id)
            week = self._find_week(state, week_index)
            task = self._find_week_task(week, task_id) if week else None
            if week is None or task is None:
                return None
            names = [c.name for c in week.concepts
                     if c.concept_id in set(task.concept_ids)] or \
                    [c.name for c in week.concepts][:6]
            content, _usage = await self._get_llm().complete(
                subtask_advisor.build_subtask_prompt(
                    state.goals_label, week.focus, task.title, names),
                max_tokens=800, disable_thinking=True)
            picks = subtask_advisor.parse_subtask_response(content)
            if not picks:
                return None
            existing = {s.title for s in task.subtasks}
            prefix = f"auto_st_{task_id}_"
            seq = 1 + max(
                (int(s.id[len(prefix):]) for s in task.subtasks
                 if s.id.startswith(prefix) and s.id[len(prefix):].isdigit()),
                default=0)
            for p in picks:
                if p["title"] in existing or len(task.subtasks) >= _MAX_SUBTASKS:
                    continue
                task.subtasks.append(SubTask(
                    id=f"{prefix}{seq}", title=p["title"], source="auto",
                    estimate_minutes=p["estimate_minutes"]))
                existing.add(p["title"])
                seq += 1
            self._save(student_id, state,
                       event=OrchestrationEvent(type="subtasks_suggested",
                           payload={"task_id": task_id,
                                    "count": len(task.subtasks)}))
            return task
        except Exception:
            return None

    def _bloom_context_safe(self, student_id: str) -> str:
        """布鲁姆认知档案薄弱项（L1 共享档案）→ 一段 prompt 上下文。空串安全。"""
        try:
            from ...core.bloom_profile import weakness_lines
            lines = weakness_lines(student_id, limit=5)
            if not lines:
                return ""
            return "学生认知层级薄弱项（布鲁姆档案，供批注参考，不要照抄）：" \
                + "；".join(lines)
        except Exception:
            return ""

    def update_schedule(self, student_id: str, *,
                        daily_minutes: int | None = None) -> dict[str, Any] | None:
        """Patch the schedule config (currently just daily_minutes).

        The next daily composition sizes slots from the new budget. Returns
        the updated schedule dict, None on failure.
        """
        try:
            state = self._load(student_id)
            if daily_minutes is not None:
                state.schedule.daily_minutes = max(5, min(480,
                                                          int(daily_minutes)))
            self._save(student_id, state,
                       event=OrchestrationEvent(type="schedule_updated",
                           payload={"daily_minutes":
                                    state.schedule.daily_minutes}))
            return state.schedule.to_dict()
        except Exception:
            return None

    def due_reviews(self, student_id: str, *,
                    now: float | None = None) -> list[dict[str, Any]]:
        """SRS-due cards for the API."""
        try:
            now = now if now is not None else time.time()
            state = self._load(student_id)
            due = srs.due_cards(state.review_queue, now=now)
            return [c.to_dict() for c in due]
        except Exception:
            return []

    # --- M-Notes SRS bridge（笔记温故与 M9 深度同步）-----------------------

    def upsert_review_card(self, student_id: str, *, concept_id: str,
                           concept_name: str = "",
                           now: float | None = None) -> dict[str, Any]:
        """注册/刷新一张复习卡；已有卡片保持 SM-2 调度状态不变。

        M-Notes 温故笔记以 concept_id="note:<note_id>" 入队；卡片进入
        review_queue 后自然流入今日任务/日计划（daily_composer 通用消费）。
        """
        try:
            cid = str(concept_id or "").strip()
            if not cid:
                return {}
            state = self._load(student_id)
            card = state.review_queue.get(cid)
            if card is None:
                card = srs.create_card(cid, concept_name=concept_name or cid,
                                       now=now)
                state.review_queue[cid] = card
                self._save(student_id, state)
            elif concept_name and card.concept_name != concept_name:
                card.concept_name = concept_name
                state.review_queue[cid] = card
                self._save(student_id, state)
            return state.review_queue.get(cid, ReviewItem()).to_dict()
        except Exception:
            return {}

    def submit_review(self, student_id: str, *, concept_id: str,
                      quality: int, now: float | None = None) -> dict[str, Any]:
        """应用一次 SM-2 反馈（如 记得5/模糊3/忘了1），返回更新后的卡片。

        自评可调日程，但与作答证据分开标记（A07）：事件带
        ``source="self_report"``，下游统计有效召回时不与 quiz 判定混算。"""
        try:
            cid = str(concept_id or "").strip()
            state = self._load(student_id)
            card = state.review_queue.get(cid)
            if card is None:
                return {}
            card = srs.update_review(card, quality, now=now)
            state.review_queue[cid] = card
            self._save(student_id, state, event=OrchestrationEvent(
                type="srs_review",
                payload={"concept_id": cid,
                         "quality": max(0, min(5, int(quality))),
                         "next_review": card.next_review,
                         "source": "self_report"}))
            return card.to_dict()
        except Exception:
            return {}

    def remove_review_card(self, student_id: str, *, concept_id: str) -> bool:
        """笔记删除/关闭温故时移除复习卡（幂等）。"""
        try:
            cid = str(concept_id or "").strip()
            state = self._load(student_id)
            if state.review_queue.pop(cid, None) is None:
                return False
            self._save(student_id, state)
            return True
        except Exception:
            return False

    def summary(self, student_id: str) -> dict[str, Any]:
        """Full orchestration state for the API, plus a needs_replan flag.

        needs_replan is a read-only staleness signal (M2 mastery read-only,
        learning_planner.needs_replan); it is surfaced for the UI banner but
        never triggers an automatic re-plan. Defaults to False on any
        failure. Never raises.
        """
        out = store.state_summary(student_id)
        try:
            state = self._load(student_id)
            out["needs_replan"] = learning_planner.needs_replan(
                state, self._mastery_view_safe())
        except Exception:
            out["needs_replan"] = False
        return out

    # --- M2/M3/M5/M6 read-only projections (guarded) --------------------

    def _get_llm(self):
        """The shared async LLM client (imported lazily so tests can patch
        app.core.llm_async.get_llm and module import stays light)."""
        from ...core.llm_async import get_llm
        return get_llm()

    def _concept_names_safe(self, subject: str = "",
                            student_id: str = "") -> dict[str, str]:
        """Read M5/M2 graph node names (read-only). Empty dict on failure."""
        try:
            from ..student_model import get_student_model, is_enabled
            from ..student_model.store import DEFAULT_STUDENT_ID
            if is_enabled():
                sm = get_student_model(student_id or DEFAULT_STUDENT_ID)
                return {nid: node.name
                        for nid, node in sm.graph.nodes.items()
                        if not subject or node.subject == subject}
        except Exception:
            pass
        return {}

    def _mastery_view_safe(self, student_id: str = "") -> dict[str, Any]:
        """Read M2 mastery view (read-only). Returns {} on any failure.

        `student_id` keys the mastery namespace -- passing the caller's id is
        required, otherwise a logged-in user's plan/composition silently
        reads the guest namespace (and needlessly cold-builds a second one).
        """
        try:
            from ..student_model import get_student_model, is_enabled
            from ..student_model.store import DEFAULT_STUDENT_ID
            if is_enabled():
                return get_student_model(
                    student_id or DEFAULT_STUDENT_ID).mastery_view()
        except Exception:
            pass
        return {}

    def habit_patterns(self, student_id: str, *,
                       subject: str = "") -> list[dict[str, Any]]:
        """Read M6 long-term habit patterns (read-only). Returns [] on failure.

        M9 reads the AUTHORITATIVE HabitPatternMemory from M6 (folded from the
        four orchestration event types); M9 never owns these. Real consumer:
        the daily LLM composer renders them as pacing context
        (daily_composer.habit_context) — the write side is no longer
        write-only.
        """
        try:
            from ..memory import is_enabled as mem_on
            if mem_on():
                from ..memory.habit_pattern import read_habit_patterns
                return read_habit_patterns(student_id, subject=subject)
        except Exception:
            pass
        return []

    def _compose_context_safe(self, student_id: str) -> str:
        """Grounded context for the daily compose prompt: Bloom weaknesses +
        M6 long-term habits. Empty string when neither has data. Never raises.
        """
        try:
            parts = [p for p in (
                self._bloom_context_safe(student_id),
                daily_composer.habit_context(self.habit_patterns(student_id)),
            ) if p]
            return "\n".join(parts)
        except Exception:
            return ""

    def _subject_skills_safe(self, subject: str,
                             student_id: str = "") -> list[dict[str, Any]]:
        """Read M2/M5 skill graph nodes for a subject (read-only).

        Returns [{skill_id, name, subject, difficulty}] for the goal analyzer's
        gap analysis. Empty list on any failure (degrades to empty GoalState).
        """
        try:
            from ..student_model import get_student_model, is_enabled
            from ..student_model.store import DEFAULT_STUDENT_ID
            if is_enabled():
                sm = get_student_model(student_id or DEFAULT_STUDENT_ID)
                out = []
                for nid, node in sm.graph.nodes.items():
                    if subject and node.subject != subject:
                        continue
                    out.append({"skill_id": nid, "name": node.name,
                                "subject": node.subject,
                                "difficulty": node.difficulty})
                return out
        except Exception:
            pass
        return []

    def _prereq_map_safe(self, student_id: str = "") -> dict[str, list[str]]:
        """Read M2/M5 prerequisite map (read-only). Empty dict on failure."""
        try:
            from ..student_model import get_student_model, is_enabled
            from ..student_model.store import DEFAULT_STUDENT_ID
            if is_enabled():
                sm = get_student_model(student_id or DEFAULT_STUDENT_ID)
                return {nid: list(node.prerequisites)
                        for nid, node in sm.graph.nodes.items()}
        except Exception:
            pass
        return {}

    def _concept_chain_skills_safe(self, target_ids: list[str],
                                   student_id: str = "") -> list[dict[str, Any]]:
        """Read the goal's prerequisite-closure skills (read-only M2/M5).

        Returns [{skill_id, name, subject, difficulty}] over the unmastered
        prerequisite chain of the target concepts (capped, deterministic).
        Empty list on any failure -> the caller falls back to subject mode.
        """
        try:
            from ..student_model import get_student_model, is_enabled
            from ..student_model.store import DEFAULT_STUDENT_ID
            if not is_enabled() or not target_ids:
                return []
            sm = get_student_model(student_id or DEFAULT_STUDENT_ID)
            mastery_view = self._mastery_view_safe(student_id)
            mastered = {
                sid for sid, rec in (mastery_view or {}).items()
                if isinstance(rec, dict) and float(rec.get("p_known", 0) or 0) >= 0.75}
            prereq_map = {nid: list(node.prerequisites)
                          for nid, node in sm.graph.nodes.items()}
            closure = goal_analyzer.prerequisite_closure(
                target_ids, prereq_map, mastered)
            out = []
            for nid in closure:
                node = sm.graph.nodes.get(nid)
                if node is None:
                    continue
                out.append({"skill_id": nid, "name": node.name,
                            "subject": node.subject,
                            "difficulty": node.difficulty})
            return out
        except Exception:
            return []

    def _analyze_goals_safe(self, state: OrchestrationState,
                            now: float | None = None,
                            *, student_id: str = "") -> None:
        """Run GoalAnalyzer for EVERY goal, rebuilding state.goal_states (1:1
        by goal_id; best-effort, read-only over M2/M5). Never raises.

        Per goal, two口径 in priority order:
          1. concept_chain -- the goal has target_concept_ids bound: gaps and
             progress run over the goal's prerequisite closure (what the
             TARGET actually needs, multi-subject naturally).
          2. subject -- legacy whole-subject analysis, used when no concepts
             are bound. Requires a resolvable subject; an empty subject with
             no keyword hit no longer degrades to whole-graph analysis (that
             was a bug: an unfiltered 1400-node "gap" list).
        A goal the analyzer cannot resolve keeps its previous state when one
        exists, otherwise gets a title-carrying default (so the UI can still
        pair goal ↔ state). A missing graph degrades to defaults.
        """
        try:
            now = now if now is not None else time.time()
            mastery_view = self._mastery_view_safe(student_id)
            prereq_map = self._prereq_map_safe(student_id)

            states: list[GoalState] = []
            for goal in state.goals:
                prev = state.goal_state_for(goal.id)
                gs = self._analyze_one_goal(
                    state, goal, mastery_view=mastery_view,
                    prereq_map=prereq_map, now=now, student_id=student_id)
                if gs is None:
                    gs = prev or GoalState(
                        goal_id=goal.id, goal_title=goal.title,
                        goal_type=goal.goal_type, subject=goal.subjects[0]
                        if goal.subjects else "",
                        deadline=goal.deadline,
                        target_concept_ids=list(goal.target_concept_ids or []))
                states.append(gs)
            state.goal_states = states
        except Exception:
            pass

    def _analyze_one_goal(self, state: OrchestrationState, goal: LearningGoal,
                          *, mastery_view: dict[str, Any],
                          prereq_map: dict[str, list[str]],
                          now: float, student_id: str = "") -> GoalState | None:
        """Analyze one goal; None when nothing resolvable (caller keeps the
        previous/default state). Never raises."""
        try:
            binding = [c for c in (goal.target_concept_ids or []) if c]
            if binding:
                skills = self._concept_chain_skills_safe(
                    binding, student_id=student_id)
                if skills:
                    return goal_analyzer.compute_gap_analysis(
                        goal, subject_skills=skills,
                        mastery_view=mastery_view, prereq_map=prereq_map,
                        now=now, chain_mode="concept_chain",
                        weekly_pace=learning_planner._MAX_CONCEPTS_PER_WEEK)
                # bound concepts unresolvable in the graph -> fall through
                # to subject mode rather than an empty state

            subject = (goal.subjects[0] if goal.subjects
                       else goal_analyzer.parse_goal_text(
                           goal.title).get("subject", ""))
            if not subject:
                return None  # no binding + no resolvable subject
            skills = self._subject_skills_safe(subject, student_id=student_id)
            if not skills:
                return None  # no graph data
            return goal_analyzer.compute_gap_analysis(
                goal, subject_skills=skills, mastery_view=mastery_view,
                prereq_map=prereq_map, now=now, chain_mode="subject",
                weekly_pace=learning_planner._MAX_CONCEPTS_PER_WEEK)
        except Exception:
            return None

    def _assemble_plan_inputs(self, student_id: str, state: OrchestrationState,
                              now: float) -> dict[str, Any]:
        """Assemble the read-only projections M3 build_learning_path needs.

        This is the REUSE point: instead of reimplementing learning-path
        inference, we call M3's planner with inputs gathered from M2/M3/M5.
        All reads are guarded so a disabled layer degrades gracefully.
        """
        mastery_view = self._mastery_view_safe(student_id)

        next_learnable: list[dict[str, Any]] = []
        review_candidates: list[dict[str, Any]] = []
        prereq_map: dict[str, list[str]] = {}

        try:
            from ..student_model import get_student_model, is_enabled
            from ..student_model.store import DEFAULT_STUDENT_ID
            if is_enabled():
                sm = get_student_model(student_id or DEFAULT_STUDENT_ID)
                # multi-goal: next-learnable across every goal's subjects;
                # 0 or 2+ distinct subjects -> whole-graph frontier (None)
                subjects = {s for g in state.goals for s in g.subjects if s}
                subject = next(iter(subjects)) if len(subjects) == 1 else ""
                # next-learnable from the skill graph (read-only)
                for n in sm.graph.next_learnable(subject or None, mastery_view,
                                                  limit=8):
                    next_learnable.append({"name": n.name, "skill_id": n.id,
                                           "difficulty": n.difficulty})
                # review candidates: seen concepts with middling mastery
                for rid, m in sm.mastery.records.items():
                    if m.attempts > 0 and 0.3 <= m.p_known < 0.8:
                        node = sm.graph.get(rid)
                        review_candidates.append({
                            "name": node.name if node else rid,
                            "skill_id": rid, "mastery": m.p_known,
                            "last_review": m.last_review,
                            "difficulty": node.difficulty if node else 3})
                # prereq map for topo-sort
                for nid, node in sm.graph.nodes.items():
                    prereq_map[nid] = list(node.prerequisites)
        except Exception:
            pass

        # if no graph data, still reuse M3's planner with empty inputs
        # (it returns a plan centred on the current concept)
        return {
            "next_learnable": next_learnable,
            "review_candidates": review_candidates,
            "mastery_view": mastery_view,
            "prereq_map": prereq_map,
        }


def _merge_user_plan(old_weeks: list[WeeklyPlan],
                     new_weeks: list[WeeklyPlan]) -> list[WeeklyPlan]:
    """Merge regenerated auto weeks with everything the user built.

    The human-override contract applied to the plan hierarchy:
      - origin=user weeks survive whole (appended after the auto set);
      - source=user WeekTasks inside auto weeks are carried onto the new
        auto week with the same week_start (matched by week window), unless
        an auto task already has the same title;
      - week_index is reassigned sequentially by week_start afterwards, so
        the plan stays ordered. Daily tasks are never touched here.
    Pure function over plain data; never raises.
    """
    try:
        # match old/new auto weeks by their 7-day bucket: week_start carries
        # the sub-second fraction of the planning moment, so exact/rounded
        # equality is unreliable across two planning runs.
        by_bucket = {int(w.week_start // _WEEK_SECONDS): w for w in old_weeks}
        out: list[WeeklyPlan] = []
        for w in new_weeks:
            old = by_bucket.get(int(w.week_start // _WEEK_SECONDS))
            if old is not None:
                existing = {t.title for t in w.tasks}
                for t in old.tasks:
                    if t.source == "user" and t.title not in existing \
                            and len(w.tasks) < _MAX_WEEK_TASKS:
                        w.tasks.append(t)
            out.append(w)
        for w in old_weeks:
            if w.origin == "user":
                out.append(w)
        out.sort(key=lambda w: w.week_start)
        for i, w in enumerate(out):
            w.week_index = i
            for pc in w.concepts:
                pc.week_index = i
        return out
    except Exception:
        return list(new_weeks)


_SERVICE = None


def get_orchestration_service() -> LearningOrchestrationService:
    """Return the process-wide singleton."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = LearningOrchestrationService.get()
    return _SERVICE
