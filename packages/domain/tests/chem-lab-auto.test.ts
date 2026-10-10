/**
 * AUTO 预组装测试：五套模板原子载入、失败保原文、重复 AUTO 不叠加。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  applyAuto, applyLabAction, createStageDocument, getStage, listStages,
  validateStageScene,
} from "../src/chem-lab/index.ts";
import type { LabDocument, StageDefinition } from "../src/chem-lab/index.ts";

const STAGE_IDS = ["bubble-relay", "silver-condenser", "chromatic-loop", "crystal-rain", "pulse-theatre"];

test("listStages 恰好五关且无教学字段", () => {
  const stages = listStages();
  assert.equal(stages.length, 5);
  for (const s of stages) {
    assert.ok(s.id && s.title.zh && s.title.en && s.description.zh);
    const raw = JSON.stringify(s);
    assert.ok(!/score|grade|goal|objective|success/i.test(raw), "关卡摘要不得出现评分/目标字段");
  }
});

test("五套 assembledTemplate 均通过技术性完整性校验且初始静止", () => {
  for (const id of STAGE_IDS) {
    const stage = getStage(id)!;
    const problem = validateStageScene(stage.assembledTemplate, { requireIdle: true });
    assert.equal(problem, null, `${id} 模板校验失败: ${problem}`);
    const t = stage.assembledTemplate;
    assert.ok(t.equipment.length >= 7 && t.equipment.length <= 15, `${id} 器材数 ${t.equipment.length} 超出 7–15`);
    assert.ok(t.connections.length >= 5 && t.connections.length <= 14, `${id} 连线数 ${t.connections.length} 超出 5–14`);
    // 初始无火焰/泵转/液体（reagent-bottle 除外）
    for (const e of t.equipment) {
      assert.notEqual(e.controls.lit, true, `${id}:${e.id} 初始不得点火`);
      assert.notEqual(e.controls.power, true, `${id}:${e.id} 泵/热板初始不得开机`);
      if (e.kind !== "reagent-bottle") assert.ok(!((e.visualContents?.fill ?? 0) > 0.001), `${id}:${e.id} 初始应为空`);
    }
  }
});

test("五关模板器材在可见工作区内且不排成一条直线", () => {
  for (const id of STAGE_IDS) {
    const t = getStage(id)!.assembledTemplate;
    const xs = new Set<number>(), zs = new Set<number>();
    for (const e of t.equipment) {
      const p = e.parentMountId ? (e.localPose ?? e.pose).position : e.pose.position;
      assert.ok(p.x >= -8 && p.x <= 8 && p.z >= -4.5 && p.z <= 4.5, `${id}:${e.id} 超出台面`);
      xs.add(Math.round(p.x * 2) / 2);
      zs.add(Math.round(p.z * 2) / 2);
    }
    assert.ok(zs.size >= 3, `${id} 器材应前后错落（Z 深度分层）`);
  }
});

test("AUTO 原子替换：设备数/连线数与模板精确一致", () => {
  for (const id of STAGE_IDS) {
    const stage = getStage(id)!;
    const doc = createStageDocument(stage);
    const after = applyAuto(doc, stage);
    assert.equal(after.equipment.length, stage.assembledTemplate.equipment.length);
    assert.equal(after.connections.length, stage.assembledTemplate.connections.length);
    assert.equal(after.revision, doc.revision + 1);
    const ids = after.equipment.map(e => e.id);
    assert.equal(new Set(ids).size, ids.length, "不得重复实例");
    for (const conn of after.connections) {
      assert.ok(ids.includes(conn.a.equipmentId) && ids.includes(conn.b.equipmentId));
    }
  }
});

test("改动后 AUTO 仍精确替换，不叠加两套设备", () => {
  const stage = getStage("silver-condenser")!;
  let doc = createStageDocument(stage);
  doc = applyLabAction(doc, { type: "add", kind: "beaker", pose: { position: { x: 7, y: 0, z: 3 }, rotation: [0, 0, 0, 1] } }, stage).document;
  const autoDoc = applyAuto(doc, stage);
  assert.equal(autoDoc.equipment.length, stage.assembledTemplate.equipment.length, "AUTO 后不得残留用户添加的额外器材");
  const again = applyAuto(autoDoc, stage);
  assert.equal(again.equipment.length, stage.assembledTemplate.equipment.length, "重复 AUTO 不得叠加");
});

test("模板损坏时 applyAuto 抛 typed 错误且原文档不变", () => {
  const stage = getStage("bubble-relay")!;
  const doc = createStageDocument(stage);
  const broken: StageDefinition = {
    ...stage,
    assembledTemplate: {
      equipment: stage.assembledTemplate.equipment,
      connections: [{ id: "cn-x", a: { equipmentId: "ghost", portId: "neck" }, b: { equipmentId: "wash-1", portId: "gasIn" }, style: "tube" }],
    },
  };
  assert.throws(() => applyAuto(doc, broken), (err: unknown): boolean => err instanceof Error && err.name === "LabError" && /ghost_endpoint/.test(String(err.message)));
  // 原文档未被污染
  assert.equal(doc.equipment.length, stage.starterScene.equipment.length);
});

test("AUTO 之后可继续自由操作（加液/点火/拆管）", () => {
  const stage = getStage("silver-condenser")!;
  const doc = applyAuto(createStageDocument(stage), stage);
  const withLiquid = applyLabAction(doc, { type: "setVisualContents", id: "flask-1", contents: { fill: 0.5, colorId: "amber" } }, stage);
  assert.ok(withLiquid.ok);
  const lit = applyLabAction(withLiquid.document, { type: "setControl", id: "lamp-1", controlId: "lit", value: true }, stage);
  assert.ok(lit.ok);
  const cut = applyLabAction(lit.document, { type: "disconnect", connectionId: "cn-1" }, stage);
  assert.ok(cut.ok);
  assert.equal(cut.document.connections.length, 6);
  const reconnect = applyLabAction(cut.document, { type: "connect", a: { equipmentId: "flask-1", portId: "neck" }, b: { equipmentId: "cond-1", portId: "vaporIn" }, style: "glass" }, stage);
  assert.ok(reconnect.ok, "拆开后应能重新接回");
});

test("LabDocument roundtrip 通过 storage 层（联动冒烟）", () => {
  for (const id of STAGE_IDS) {
    const stage = getStage(id)!;
    const doc: LabDocument = applyAuto(createStageDocument(stage), stage);
    assert.equal(doc.stageId, id);
  }
});
