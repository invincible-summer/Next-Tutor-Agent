"""Route-level tests for the chem-lab REST family."""
from __future__ import annotations

import unittest

from app.chem_lab import persistence
from app.main import create_app
from tests.support.storage_sandbox import StorageSandboxTestCase, authenticated_client

BASE = "/api/v1/tools/lab/chemistry"
OWNER = "chem_student"


def _command_body(command: dict, *, revision: int, pack_hash: str,
                  command_id: str = "cmd-1", client_seq: int = 1) -> dict:
    return {
        "command_id": command_id,
        "client_seq": client_seq,
        "base_revision": revision,
        "pack_hash": pack_hash,
        "command": command,
    }


class ChemLabApiTest(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app = create_app()
        self.client = authenticated_client(self.app, OWNER)

    # -- catalog ----------------------------------------------------------

    def test_catalog_lists_six_experiments(self) -> None:
        resp = self.client.get(f"{BASE}/catalog")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["experiments"]), 6)
        ids = {item["id"] for item in data["experiments"]}
        self.assertIn("chem.dilution", ids)
        self.assertTrue(all(item["pack_hash"] for item in data["experiments"]))

    def test_experiment_detail_is_public_projection(self) -> None:
        resp = self.client.get(f"{BASE}/experiments/chem.dilution")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["id"], "chem.dilution")
        self.assertTrue(data["pack_hash"])
        # 预测答案绝不出现在公开投影里。
        for prediction in data["predictions"]:
            for option in prediction["options"]:
                self.assertNotIn("correct", option)

    def test_experiment_missing_is_404(self) -> None:
        resp = self.client.get(f"{BASE}/experiments/chem.nope")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"]["code"], "chem_lab_experiment_missing")

    def test_engine_pack_strips_prediction_answers(self) -> None:
        resp = self.client.get(f"{BASE}/experiments/chem.acid_base_indicator/engine-pack")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["pack_hash"])
        pack = data["pack"]
        # 引擎镜像所需的私有定义齐全。
        self.assertTrue(pack["_rules"])
        self.assertTrue(pack["_species_defs"])
        self.assertTrue(pack["_equipment_defs"])
        for prediction in pack["predictions"]:
            for option in prediction["options"]:
                self.assertNotIn("correct", option)

    def test_session_snapshot_carries_engine_state(self) -> None:
        snap = self._create_session()
        engine_state = snap["engine_state"]
        self.assertIsInstance(engine_state, dict)
        self.assertEqual(engine_state["revision"], snap["revision"])
        self.assertEqual(engine_state["state_hash"], snap["state_hash"])
        self.assertIn("equipment", engine_state)

    # -- session lifecycle --------------------------------------------------

    def _create_session(self, experiment: str = "chem.dilution", mode: str = "guided") -> dict:
        resp = self.client.post(f"{BASE}/sessions", json={
            "experiment_id": experiment, "mode": mode, "language": "zh", "session_seed": 0,
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_create_session_rejects_owner_smuggling(self) -> None:
        resp = self.client.post(f"{BASE}/sessions", json={
            "experiment_id": "chem.dilution", "mode": "guided",
            "language": "zh", "session_seed": 0, "owner": "someone_else",
        })
        self.assertEqual(resp.status_code, 422)

    def test_create_and_get_session(self) -> None:
        snap = self._create_session()
        self.assertEqual(snap["phase"], "ready")
        self.assertEqual(snap["revision"], 0)
        self.assertTrue(snap["state_hash"])
        self.assertEqual(snap["mode"], "guided")
        resp = self.client.get(f"{BASE}/sessions/{snap['session_id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["state_hash"], snap["state_hash"])

    def test_list_sessions_pagination(self) -> None:
        self._create_session()
        resp = self.client.get(f"{BASE}/sessions")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(len(data["items"]), 1)
        self.assertIsNone(data["next_cursor"])

    # -- commands -------------------------------------------------------------

    def _first_command(self, snap: dict) -> dict:
        return _command_body(
            {"kind": "aspirate", "source_id": "stock", "instrument_id": "pipette-1",
             "amount_uL": 10000},
            revision=snap["revision"], pack_hash=snap["pack_hash"])

    def test_command_happy_path(self) -> None:
        snap = self._create_session()
        resp = self.client.post(f"{BASE}/sessions/{snap['session_id']}/commands",
                                json=self._first_command(snap))
        self.assertEqual(resp.status_code, 200, resp.text)
        ack = resp.json()
        self.assertTrue(ack["accepted"])
        self.assertEqual(ack["revision"], snap["revision"] + 1)
        self.assertTrue(ack["state_hash"])
        self.assertNotEqual(ack["state_hash"], snap["state_hash"])
        self.assertEqual(ack["error_code"], "")
        self.assertGreater(len(ack["events"]), 0)
        self.assertIsNotNone(ack["guidance"])
        self.assertIsNotNone(ack["render_frame"])

    def test_command_duplicate_returns_original_ack(self) -> None:
        snap = self._create_session()
        body = self._first_command(snap)
        first = self.client.post(
            f"{BASE}/sessions/{snap['session_id']}/commands", json=body).json()
        # 同一 command_id 重试：revision 不变，返回原始 ACK（事件从日志重建）。
        second = self.client.post(
            f"{BASE}/sessions/{snap['session_id']}/commands", json=body).json()
        self.assertEqual(first, second)
        self.assertEqual(second["revision"], first["revision"])

    def test_command_revision_conflict(self) -> None:
        snap = self._create_session()
        self.client.post(f"{BASE}/sessions/{snap['session_id']}/commands",
                         json=self._first_command(snap))
        stale = self._first_command(snap)
        stale["command_id"] = "cmd-2"
        resp = self.client.post(f"{BASE}/sessions/{snap['session_id']}/commands", json=stale)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["detail"]["code"], "chem_lab_revision_conflict")

    def test_command_pack_conflict(self) -> None:
        snap = self._create_session()
        body = self._first_command(snap)
        body["pack_hash"] = "sha256:wrong"
        resp = self.client.post(f"{BASE}/sessions/{snap['session_id']}/commands", json=body)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["detail"]["code"], "chem_lab_pack_conflict")

    def test_command_rejection_is_displayable(self) -> None:
        snap = self._create_session()
        body = _command_body(
            {"kind": "pour", "source_id": "beaker-a", "target_id": "beaker-b",
             "amount_uL": 5000},
            revision=snap["revision"], pack_hash=snap["pack_hash"])
        resp = self.client.post(f"{BASE}/sessions/{snap['session_id']}/commands", json=body)
        self.assertEqual(resp.status_code, 200)
        ack = resp.json()
        self.assertFalse(ack["accepted"])
        self.assertTrue(ack["error_code"].startswith("chem_lab_"))
        self.assertEqual(ack["guidance"]["level"], "try_again")
        kinds = {event["kind"] for event in ack["events"]}
        self.assertIn("command_rejected", kinds)

    def test_events_endpoint_pages_after_seq(self) -> None:
        snap = self._create_session()
        self.client.post(f"{BASE}/sessions/{snap['session_id']}/commands",
                         json=self._first_command(snap))
        resp = self.client.get(f"{BASE}/sessions/{snap['session_id']}/events",
                               params={"after_seq": 0})
        self.assertEqual(resp.status_code, 200)
        page = resp.json()
        self.assertGreater(len(page["items"]), 0)
        last = page["items"][-1]["seq"]
        resp2 = self.client.get(f"{BASE}/sessions/{snap['session_id']}/events",
                                params={"after_seq": last})
        self.assertEqual(resp2.json()["items"], [])

    # -- checkpoints / branches ----------------------------------------------

    def test_checkpoint_and_fork(self) -> None:
        snap = self._create_session()
        sid = snap["session_id"]
        self.client.post(f"{BASE}/sessions/{sid}/commands", json=self._first_command(snap))
        resp = self.client.post(f"{BASE}/sessions/{sid}/checkpoints", json={"label": " midpoint "})
        self.assertEqual(resp.status_code, 200, resp.text)
        ack = resp.json()
        self.assertTrue(ack["accepted"])
        cp_event = next(e for e in ack["events"] if e["kind"] == "checkpoint_created")
        cp_id = cp_event["data"]["checkpoint_id"]

        fork = self.client.post(f"{BASE}/sessions/{sid}/fork",
                                json={"checkpoint_id": cp_id})
        self.assertEqual(fork.status_code, 200, fork.text)
        result = fork.json()
        branch = result["session"]
        self.assertNotEqual(branch["session_id"], sid)
        self.assertEqual(branch["branch"]["parent_session_id"], sid)
        self.assertEqual(result["source_session_id"], sid)

        # 分支独立演进：在分支里继续命令不影响源会话。
        branch_cmd = _command_body(
            {"kind": "pour", "source_id": "water-bottle", "target_id": "beaker-a",
             "amount_uL": 90000, "rate": "normal"},
            revision=branch["revision"], pack_hash=branch["pack_hash"], command_id="cmd-b1")
        resp2 = self.client.post(f"{BASE}/sessions/{branch['session_id']}/commands",
                                 json=branch_cmd)
        self.assertEqual(resp2.status_code, 200, resp2.text)
        self.assertTrue(resp2.json()["accepted"])

    def test_reset_creates_branch_from_start(self) -> None:
        snap = self._create_session()
        sid = snap["session_id"]
        self.client.post(f"{BASE}/sessions/{sid}/commands", json=self._first_command(snap))
        resp = self.client.post(f"{BASE}/sessions/{sid}/reset", json={})
        self.assertEqual(resp.status_code, 200, resp.text)
        branch = resp.json()["session"]
        self.assertEqual(branch["revision"], 0)
        self.assertEqual(branch["phase"], "ready")

    # -- finish / delete --------------------------------------------------------

    def _complete_dilution(self, sid: str, snap: dict) -> dict:
        revision = snap["revision"]
        pack_hash = snap["pack_hash"]
        commands = [
            {"kind": "aspirate", "source_id": "stock", "instrument_id": "pipette-1",
             "amount_uL": 10000},
            {"kind": "dispense", "instrument_id": "pipette-1", "target_id": "beaker-a",
             "amount_uL": 10000},
            {"kind": "pour", "source_id": "water-bottle", "target_id": "beaker-a",
             "amount_uL": 200000},
            {"kind": "pour", "source_id": "beaker-a", "target_id": "cylinder",
             "amount_uL": 50000},
            {"kind": "measure", "instrument_id": "cylinder", "vessel_id": "cylinder",
             "quantity": "volume"},
        ]
        ack = None
        for index, command in enumerate(commands):
            resp = self.client.post(f"{BASE}/sessions/{sid}/commands", json=_command_body(
                command, revision=revision, pack_hash=pack_hash,
                command_id=f"cmd-c{index}", client_seq=index + 1))
            self.assertEqual(resp.status_code, 200, resp.text)
            ack = resp.json()
            self.assertTrue(ack["accepted"], f"{command} -> {ack}")
            revision = ack["revision"]
        return ack

    def test_finish_returns_result_card(self) -> None:
        snap = self._create_session()
        sid = snap["session_id"]
        self._complete_dilution(sid, snap)
        resp = self.client.post(f"{BASE}/sessions/{sid}/finish")
        self.assertEqual(resp.status_code, 200, resp.text)
        card = resp.json()
        self.assertTrue(all(goal["status"] == "met" for goal in card["goals"]))
        self.assertTrue(card["observations"])
        # 幂等：重复 finish 返回同一张结果卡。
        again = self.client.post(f"{BASE}/sessions/{sid}/finish")
        self.assertEqual(again.json(), card)

    def test_delete_blocks_late_writes(self) -> None:
        snap = self._create_session()
        sid = snap["session_id"]
        resp = self.client.delete(f"{BASE}/sessions/{sid}")
        self.assertEqual(resp.json(), {"deleted": True})
        late = self.client.post(f"{BASE}/sessions/{sid}/commands",
                                json=self._first_command(snap))
        self.assertEqual(late.status_code, 404)
        self.assertEqual(late.json()["detail"]["code"], "chem_lab_session_missing")
        # 墓碑阻止复活：事件文件已清除。
        self.assertFalse(persistence.events_path(OWNER, sid).exists())

    # -- isolation --------------------------------------------------------------

    def test_purge_account_erases_sessions_and_blocks_late_writes(self) -> None:
        snap = self._create_session()
        sid = snap["session_id"]
        from app.core.account_data import purge_account
        purge_account(OWNER)
        # 账号记录已删除：API 层认证失败关闭（401）。
        resp = self.client.get(f"{BASE}/sessions/{sid}")
        self.assertEqual(resp.status_code, 401)
        # 存储层：即使绕过认证，迟到写也被墓碑/缺文档拒绝。
        from app.chem_lab import service as chem_service
        from app.chem_lab.errors import ChemLabError
        with self.assertRaises(ChemLabError) as ctx:
            chem_service.get_session(OWNER, sid)
        self.assertEqual(ctx.exception.code, "session_missing")
        # owner 目录已清除，epoch 墓碑仍在（fail-closed）。
        self.assertFalse(persistence.owner_dir(OWNER).exists())
        self.assertGreaterEqual(persistence.epoch(OWNER), 1)

    def test_owner_isolation(self) -> None:
        snap = self._create_session()
        other = authenticated_client(self.app, "chem_other")
        resp = other.get(f"{BASE}/sessions/{snap['session_id']}")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
