/**
 * 文档层测试：装备增删/移动/旋转/挂载脱挂/多级父子变换。
 * 不测化学正确性——那是已取消的体系。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  applyLabAction, createHistory, createStageDocument, instanceWorldPose,
  normalizeInstance, pushHistory, undoHistory,
} from "../src/chem-lab/index.ts";
import { getStage } from "../src/chem-lab/index.ts";

const stage = getStage("bubble-relay")!;

function freshDoc() { return createStageDocument(stage); }

test("createStageDocument 从 starterScene 初始化且不自动装配", () => {
  const doc = freshDoc();
  assert.equal(doc.schemaVersion, 1);
  assert.equal(doc.stageId, "bubble-relay");
  assert.ok(doc.equipment.length > 0);
  assert.equal(doc.connections.length, 0);
  assert.ok(doc.equipment.every(e => e.parentMountId === undefined));
});

test("add 产生稳定唯一 ID 且超限拒绝", () => {
  const doc = freshDoc();
  const r1 = applyLabAction(doc, { type: "add", kind: "beaker", pose: { position: { x: 1, y: 0, z: 1 }, rotation: [0, 0, 0, 1] } }, stage);
  assert.ok(r1.ok);
  const r2 = applyLabAction(r1.document, { type: "add", kind: "beaker", pose: { position: { x: 2, y: 0, z: 1 }, rotation: [0, 0, 0, 1] } }, stage);
  assert.ok(r2.ok);
  const ids = r2.document.equipment.map(e => e.id);
  assert.equal(new Set(ids).size, ids.length, "ID 必须唯一");
  const bad = applyLabAction(doc, { type: "add", kind: "not-a-thing", pose: { position: { x: 0, y: 0, z: 0 }, rotation: [0, 0, 0, 1] } }, stage);
  assert.equal(bad.ok, false);
  assert.equal(bad.document, doc, "失败必须保留原文档");
});

test("move/rotate 更新顶层与挂载件局部姿态", () => {
  let doc = freshDoc();
  doc = applyLabAction(doc, { type: "add", kind: "beaker", pose: { position: { x: 1, y: 0, z: 1 }, rotation: [0, 0, 0, 1] } }, stage).document;
  const beakerId = doc.equipment.find(e => e.kind === "beaker")!.id;
  const moved = applyLabAction(doc, { type: "move", id: beakerId, pose: { position: { x: 3, y: 0, z: -2 }, rotation: [0, 0, 0, 1] } }, stage);
  assert.ok(moved.ok);
  assert.deepEqual(moved.document.equipment.find(e => e.id === beakerId)!.pose.position, { x: 3, y: 0, z: -2 });

  const rotated = applyLabAction(moved.document, { type: "rotate", id: beakerId, rotation: [0, Math.SQRT1_2, 0, Math.SQRT1_2] }, stage);
  assert.ok(rotated.ok);
  assert.deepEqual(rotated.document.equipment.find(e => e.id === beakerId)!.pose.rotation, [0, Math.SQRT1_2, 0, Math.SQRT1_2]);
});

test("attach 挂载到铁架台、detach 恢复自由姿态、父级环被拒绝", () => {
  let doc = freshDoc();
  doc = applyLabAction(doc, { type: "add", kind: "stand", pose: { position: { x: 0, y: 0, z: 0 }, rotation: [0, 0, 0, 1] } }, stage).document;
  doc = applyLabAction(doc, { type: "add", kind: "flask-round", pose: { position: { x: 2, y: 0, z: 0 }, rotation: [0, 0, 0, 1] } }, stage).document;
  const standId = doc.equipment.filter(e => e.kind === "stand").map(e => e.id).at(-1)!;
  const flaskId = doc.equipment.filter(e => e.kind === "flask-round").map(e => e.id).at(-1)!;
  const standWorldX = doc.equipment.find(e => e.id === standId)!.pose.position.x;

  const attached = applyLabAction(doc, {
    type: "attach", id: flaskId, parentMountId: standId,
    localPose: { position: { x: -0.5, y: 1.5, z: -0.3 }, rotation: [0, 0, 0, 1] },
  }, stage);
  assert.ok(attached.ok);
  const flask = attached.document.equipment.find(e => e.id === flaskId)!;
  assert.equal(flask.parentMountId, standId);

  // 世界姿态由 parent + localPose 递归求出
  const world = instanceWorldPose(attached.document, flaskId)!;
  assert.equal(world.position.x, standWorldX - 0.5);
  assert.equal(world.position.y, 1.5);

  // 不可挂载到非支架
  const badMount = applyLabAction(attached.document, {
    type: "attach", id: standId, parentMountId: flaskId,
    localPose: { position: { x: 0, y: 0, z: 0 }, rotation: [0, 0, 0, 1] },
  }, stage);
  assert.equal(badMount.ok, false);

  const detached = applyLabAction(attached.document, {
    type: "detach", id: flaskId, pose: { position: { x: 4, y: 0, z: 2 }, rotation: [0, 0, 0, 1] },
  }, stage);
  assert.ok(detached.ok);
  const free = detached.document.equipment.find(e => e.id === flaskId)!;
  assert.equal(free.parentMountId, undefined);
  assert.deepEqual(free.pose.position, { x: 4, y: 0, z: 2 });
});

test("remove 支架时挂载子件落地、相关连线移除", () => {
  const silverStage = getStage("silver-condenser")!;
  let doc = createStageDocument(silverStage);
  doc = applyLabAction(doc, { type: "replaceAuto" }, silverStage).document;
  const stand1 = doc.equipment.find(e => e.id === "stand-1")!;
  const flask = doc.equipment.find(e => e.id === "flask-1")!;
  assert.equal(flask.parentMountId, stand1.id, "模板中烧瓶应挂在支架上");

  // 移除支架：挂载子件脱挂落地，其软管连接保留（管随物体，不凭空消失）
  const removedStand = applyLabAction(doc, { type: "remove", id: stand1.id }, silverStage);
  assert.ok(removedStand.ok);
  const after = removedStand.document;
  assert.ok(!after.equipment.some(e => e.id === stand1.id));
  const landedFlask = after.equipment.find(e => e.id === flask.id)!;
  assert.equal(landedFlask.parentMountId, undefined, "子件不得悬空");
  assert.equal(landedFlask.pose.position.y, 0, "脱挂后落回台面高度");
  assert.ok(after.connections.some(c => c.id === "cn-1"), "落台烧瓶的软管连接应保留");

  // 移除有连线的器材：其连线应一并移除
  const removedFlask = applyLabAction(after, { type: "remove", id: flask.id }, silverStage);
  assert.ok(removedFlask.ok);
  assert.ok(!removedFlask.document.connections.some(c => c.id === "cn-1"), "涉及被删器材的连线应一并移除");
});

test("挂载件移动写为局部姿态且支架移动带动子件世界位置", () => {
  const silverStage = getStage("silver-condenser")!;
  let doc = createStageDocument(silverStage);
  doc = applyLabAction(doc, { type: "replaceAuto" }, silverStage).document;
  const flaskBefore = instanceWorldPose(doc, "flask-1")!;
  // 移动支架（顶层）
  const stand1 = doc.equipment.find(e => e.id === "stand-1")!;
  doc = applyLabAction(doc, {
    type: "move", id: stand1.id,
    pose: { position: { x: stand1.pose.position.x + 1.2, y: 0, z: stand1.pose.position.z }, rotation: stand1.pose.rotation },
  }, silverStage).document;
  const flaskAfter = instanceWorldPose(doc, "flask-1")!;
  assert.ok(Math.abs(flaskAfter.position.x - flaskBefore.position.x - 1.2) < 1e-6, "挂载子件应随支架平移");
});

test("normalizeInstance 补全控制默认值", () => {
  const instance = normalizeInstance({ id: "x", kind: "pump", pose: { position: { x: 0, y: 0, z: 0 }, rotation: [0, 0, 0, 1] } });
  assert.equal(instance.controls.power, false);
  assert.equal(instance.controls.speed, 0.7);
});

test("轻量撤销：push/undo 往返且上限截断", () => {
  let doc = freshDoc();
  let history = createHistory();
  for (let i = 0; i < 30; i++) {
    history = pushHistory(history, doc);
    doc = applyLabAction(doc, { type: "add", kind: "beaker", pose: { position: { x: i, y: 0, z: 0 }, rotation: [0, 0, 0, 1] } }, stage).document;
  }
  assert.ok(history.past.length <= 20);
  const undone = undoHistory(history, doc);
  assert.equal(undone.document.equipment.length, doc.equipment.length - 1);
});

test("reset 恢复到未搭好的初始摆放", () => {
  const doc = createStageDocument(getStage("silver-condenser")!);
  const reset = applyLabAction(doc, { type: "reset" }, getStage("silver-condenser")!);
  assert.ok(reset.ok);
  assert.equal(reset.document.connections.length, 0);
  assert.equal(reset.document.equipment.length, getStage("silver-condenser")!.starterScene.equipment.length);
});
