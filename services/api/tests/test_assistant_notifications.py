"""C04/C05 订阅调度、通知收件箱与报告回归。

覆盖：订阅 CRUD 与幂等创建；next_run 本地时区/星期计算；到期执行去重
（重复 tick 不重复投递）；停机 catch-up 只补 48h 内最新一次；静默时段
只准备不投递、出窗后释放；每日主动上限；无内容种类不发送；通知已读/
忽略与 mute；报告持久化、读取、删除与过期；调度开关关闭时 tick 无操作。
"""
from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from tests.storage_sandbox import StorageSandboxTestCase

from app.agents.site_assistant import notifications as notify
from app.agents.site_assistant import reports


def _hex() -> str:
    return uuid.uuid4().hex[:12]


class SubscriptionCrudTest(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sid = "usr_c04_" + _hex()

    def test_create_idempotent_and_defaults(self) -> None:
        crid = "cr_sub_" + _hex()
        sub = notify.create_subscription(
            self.sid, kind="weekly_brief", timezone_name="Asia/Shanghai",
            client_request_id=crid)
        self.assertEqual(sub["kind"], "weekly_brief")
        self.assertEqual(sub["weekdays"], [0])       # weekly 仅一个 weekday
        self.assertEqual(sub["local_time"], "09:00")  # 默认建议时间
        self.assertTrue(sub["enabled"])
        again = notify.create_subscription(
            self.sid, kind="weekly_brief",
            timezone_name="Asia/Shanghai", client_request_id=crid)
        self.assertEqual(again["subscription_id"],
                         sub["subscription_id"])
        with self.assertRaises(notify.SubscriptionRejected):
            notify.create_subscription(self.sid, kind="bogus",
                                       timezone_name="UTC",
                                       client_request_id="cr_x_" + _hex())

    def test_update_revision_and_delete(self) -> None:
        sub = notify.create_subscription(
            self.sid, kind="daily_tasks", timezone_name="UTC",
            local_time="08:30", client_request_id="cr_" + _hex())
        with self.assertRaises(notify.SubscriptionRejected) as ctx:
            notify.update_subscription(
                self.sid, sub["subscription_id"],
                expected_revision=999, patch={"enabled": False})
        self.assertEqual(ctx.exception.code, "revision_conflict")
        out = notify.update_subscription(
            self.sid, sub["subscription_id"],
            expected_revision=sub["revision"], patch={"enabled": False})
        self.assertFalse(out["enabled"])
        notify.delete_subscription(self.sid, sub["subscription_id"])
        # 幂等删除。
        notify.delete_subscription(self.sid, sub["subscription_id"])
        self.assertNotIn(sub["subscription_id"],
                         notify.load_subscriptions(self.sid))

    def test_next_run_local_calendar(self) -> None:
        sub = notify.create_subscription(
            self.sid, kind="weekly_brief", timezone_name="Asia/Shanghai",
            local_time="09:00", client_request_id="cr_" + _hex())
        # 以上海 2026-09-29（周二）10:00 为 now：下一个周一 09:00 本地。
        now = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        nxt = datetime.fromisoformat(sub["next_run_at"])
        self.assertEqual(nxt.weekday(), 0)
        self.assertEqual(nxt.astimezone(ZoneInfo("Asia/Shanghai")).hour, 9)
        self.assertGreater(nxt, now)
        # daily 种类：以同一 now 重算，下一运行在 24h 内且晚于 now。
        daily = notify.create_subscription(
            self.sid, kind="daily_tasks", timezone_name="Asia/Shanghai",
            local_time="09:00", client_request_id="cr_" + _hex())
        nxt2 = datetime.fromisoformat(
            notify._next_run_iso(daily, now))
        self.assertGreater(nxt2, now)
        self.assertLessEqual(nxt2 - now, timedelta(days=1))


class SchedulerTest(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sid = "usr_c04s_" + _hex()
        # 每日 09:00（UTC）订阅 daily_tasks；无任务 → skipped_empty。
        self.sub = notify.create_subscription(
            self.sid, kind="daily_tasks", timezone_name="UTC",
            local_time="09:00", client_request_id="cr_" + _hex())

    def _settings(self, enabled: bool = True):
        from app.core.config import settings
        return mock.patch.object(
            settings, "site_assistant_proactive_enabled", enabled)

    def test_tick_dedup_and_catchup(self) -> None:
        due = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
        self.sub["next_run_at"] = due.isoformat()
        notify._save_student_sub(self.sid, self.sub)
        with self._settings(True):
            out1 = notify.scheduler_tick(now=due)
            self.assertEqual(out1["skipped"], 1)   # 无任务不发送
            out2 = notify.scheduler_tick(now=due + timedelta(minutes=1))
            self.assertEqual(out2["processed"], 0)  # 已认领不重复
        # 停机超 48h：更旧 skipped，next_run 推进。
        stale = due + timedelta(days=3)
        sub2 = notify.load_subscriptions(self.sid)[self.sub["subscription_id"]]
        with self._settings(True):
            sub2["next_run_at"] = (due - timedelta(days=1)).isoformat()
            notify._save_student_sub(self.sid, sub2)
            out3 = notify.scheduler_tick(now=stale)
            self.assertEqual(out3["skipped"], 1)

    def test_disabled_flag_noops(self) -> None:
        with self._settings(False):
            out = notify.scheduler_tick(now=datetime.now(timezone.utc))
        self.assertEqual(out["processed"], 0)

    def test_daily_cap_and_delivery(self) -> None:
        now = datetime(2026, 9, 29, 9, 5, tzinfo=timezone.utc)
        # daily_tasks 有内容时投递：预置一个今日任务。
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        get_orchestration_service().add_task(
            self.sid, day="2026-09-29", title="复习力学",
            concept_id="", concept_name="", kind="study", phase="",
            priority=3, milestone_id="")
        self.sub["next_run_at"] = (
            now - timedelta(minutes=5)).isoformat()
        notify._save_student_sub(self.sid, self.sub)
        with self._settings(True):
            out = notify.scheduler_tick(now=now)
            self.assertEqual(out["delivered"], 1)
            items, total = notify.list_notifications(self.sid)
            self.assertEqual(total, 1)
            self.assertEqual(items[0]["kind"], "subscription")
            # 去重：同日重复 tick 不再投递。
            out2 = notify.scheduler_tick(now=now + timedelta(minutes=1))
            self.assertEqual(out2["delivered"], 0)
        # 每日上限：同日补 2 条已达 3，再触发 held（合成同日时刻）。
        day_iso = now.isoformat()
        notify.create_notification(self.sid, kind="subscription",
                                   title="a", summary="a",
                                   created_at=day_iso)
        notify.create_notification(self.sid, kind="subscription",
                                   title="b", summary="b",
                                   created_at=day_iso)
        sub3 = notify.load_subscriptions(self.sid)[
            self.sub["subscription_id"]]
        with self._settings(True):
            # 强制再次到期（不同 execution key）。
            sub3["revision"] += 1
            sub3["next_run_at"] = now.isoformat()
            notify._save_student_sub(self.sid, sub3)
            out3 = notify.scheduler_tick(now=now + timedelta(minutes=2))
            self.assertEqual(out3["held"], 1)

    def test_quiet_hours_hold_and_release(self) -> None:
        now = datetime(2026, 9, 29, 23, 0, tzinfo=timezone.utc)
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        get_orchestration_service().add_task(
            self.sid, day="2026-09-29", title="复习力学",
            concept_id="", concept_name="", kind="study", phase="",
            priority=3, milestone_id="")
        self.sub["next_run_at"] = now.isoformat()
        notify._save_student_sub(self.sid, self.sub)
        with self._settings(True):
            out = notify.scheduler_tick(now=now)
            self.assertEqual(out["held"], 1)     # 23:00 在 22:00–08:00 内
            _items, total = notify.list_notifications(self.sid)
            self.assertEqual(total, 0)           # 只准备，未投递
            # 次日 08:30 出窗释放（§25.3-5）。
            out2 = notify.scheduler_tick(
                now=now + timedelta(hours=9, minutes=30))
            self.assertGreaterEqual(out2["delivered"], 1)
            _items, total = notify.list_notifications(self.sid)
            self.assertEqual(total, 1)

    def test_read_dismiss_and_expiry(self) -> None:
        note = notify.create_notification(self.sid, kind="subscription",
                                          title="t", summary="s")
        out = notify.mark_notification(self.sid, note["notification_id"],
                                       action="read")
        self.assertIsNotNone(out["read_at"])
        # 幂等已读。
        out2 = notify.mark_notification(self.sid, note["notification_id"],
                                        action="read")
        self.assertEqual(out2["read_at"], out["read_at"])
        notify.mark_notification(self.sid, note["notification_id"],
                                 action="dismiss")
        _items, total = notify.list_notifications(self.sid)
        self.assertEqual(total, 0)               # 忽略后不再列出
        # 过期正文不返回（§25.5）。
        note2 = notify.create_notification(self.sid, kind="subscription",
                                           title="t2", summary="s")
        path = (notify._notifications_dir(self.sid)
                / f"{note2['notification_id']}.json")
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        data["expires_at"] = "2000-01-01T00:00:00+00:00"
        path.write_text(json.dumps(data), encoding="utf-8")
        _items, total = notify.list_notifications(self.sid)
        self.assertEqual(total, 0)


class ReportTest(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sid = "usr_c05_" + _hex()

    def test_manage_subscription_action_roundtrip(self) -> None:
        """§25.1 对话订阅：提案 → execute 建订阅（幂等）。"""
        from app.core import assistant_store as store
        from app.core.config import settings
        from app.agents.site_assistant import actions as actions_svc
        from app.agents.site_assistant import policy
        from app.agents.site_assistant.intent import parse_intent
        import asyncio
        cid = "astc_c05_" + _hex()
        store.save_conversation(self.sid, {
            "conversation_id": cid, "revision": 1, "messages": [],
            "turns": {}, "actions": {}, "accepted": {}})
        with mock.patch.object(settings,
                               "site_assistant_proactive_enabled", True):
            text = "每周一早上9点提醒我学习简报"
            parsed = asyncio.run(parse_intent(text, lang="zh"))
            self.assertEqual(parsed.kind, "prepare_action")
            proposed = policy.decide_actions(
                conversation_id=cid, turn_id="astt_c05", parsed=parsed,
                lang="zh", meta={}, results=[], page_context=None,
                page_epoch=0)
            self.assertEqual(len(proposed), 1)
            action = proposed[0]
            self.assertEqual(action["payload"]["kind"],
                             "manage_subscription")
            self.assertEqual(action["payload"]["input"]["kind"],
                             "weekly_brief")
            self.assertEqual(action["payload"]["input"]["local_time"],
                             "09:00")
            record = store.load_conversation(self.sid, cid)
            record["actions"][action["action_id"]] = action
            store.save_conversation(self.sid, record)
            out = actions_svc.execute_action(
                self.sid, action["action_id"],
                invocation_id="inv_c05", client_instance_id="c05",
                route_epoch=0)
            self.assertEqual(out["action"]["state"], "succeeded")
            sub_id = out["business_result"]["entity_id"]
            subs = notify.load_subscriptions(self.sid)
            self.assertIn(sub_id, subs)
            self.assertEqual(subs[sub_id]["kind"], "weekly_brief")
            # 幂等重试：同一订阅，不重复创建。
            out2 = actions_svc.execute_action(
                self.sid, action["action_id"],
                invocation_id="inv_c05", client_instance_id="c05",
                route_epoch=0)
            self.assertEqual(out2["business_result"]["entity_id"], sub_id)
            self.assertEqual(len(notify.load_subscriptions(self.sid)), 1)

    def test_save_load_delete_and_expiry(self) -> None:
        rid = reports.save_report(self.sid, {
            "kind": "weekly_brief",
            "window": {"start_at": "2026-09-21T00:00:00+00:00",
                       "end_at": "2026-09-28T00:00:00+00:00",
                       "timezone": "UTC", "label": "上周"},
            "learning_report": {"facts": []},
        })
        self.assertTrue(rid.startswith("astr_"))
        loaded = reports.load_report(self.sid, rid)
        self.assertEqual(loaded["kind"], "weekly_brief")
        self.assertEqual(loaded["window"]["label"], "上周")
        items, total = reports.list_reports(self.sid)
        self.assertEqual(total, 1)
        self.assertTrue(reports.delete_report(self.sid, rid))
        self.assertFalse(reports.delete_report(self.sid, rid))  # 幂等
        self.assertIsNone(reports.load_report(self.sid, rid))
        # 过期：load 返回 expired 标记，sweep 清理。
        rid2 = reports.save_report(self.sid, {"kind": "weekly_brief"})
        path = reports._reports_dir(self.sid) / f"{rid2}.json"
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        data["expires_at"] = "2000-01-01T00:00:00+00:00"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertTrue(reports.load_report(self.sid, rid2)["expired"])
        removed = reports.sweep_expired(self.sid)
        self.assertEqual(removed, 1)


if __name__ == "__main__":
    unittest.main()
