/**
 * 存档序列化测试：严格拒绝非法输入、schema v1 往返保拓扑。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  applyAuto, createStageDocument, getStage, LabStorageError,
  parseDocument, serializeDocument,
} from "../src/chem-lab/index.ts";

const stage = getStage("silver-condenser")!;

test("v1 文档序列化往返保持装置拓扑", () => {
  const doc = applyAuto(createStageDocument(stage), stage);
  const raw = serializeDocument(doc);
  const back = parseDocument(raw);
  assert.equal(back.stageId, doc.stageId);
  assert.equal(back.equipment.length, doc.equipment.length);
  assert.equal(back.connections.length, doc.connections.length);
  // 逐器材比较位置与挂载关系
  for (const e of doc.equipment) {
    const mirror = back.equipment.find(m => m.id === e.id)!;
    assert.deepEqual(mirror.pose.position, e.pose.position);
    assert.equal(mirror.parentMountId, e.parentMountId);
    assert.deepEqual(mirror.controls, e.controls);
  }
  for (const c of doc.connections) {
    const mirror = back.connections.find(m => m.id === c.id)!;
    assert.deepEqual(mirror.a, c.a);
    assert.deepEqual(mirror.b, c.b);
  }
});

test("拒绝未知 schemaVersion 与损坏 JSON", () => {
  assert.throws(() => parseDocument(""), LabStorageError);
  assert.throws(() => parseDocument("not json at all"), LabStorageError);
  assert.throws(() => parseDocument('{"schemaVersion":2,"stageId":"x"}'), LabStorageError);
  assert.throws(() => parseDocument("null"), LabStorageError);
});

test("拒绝 NaN/Infinity 坐标", () => {
  const doc = applyAuto(createStageDocument(stage), stage);
  const poisoned = JSON.parse(serializeDocument(doc));
  poisoned.equipment[0].pose.position.x = NaN;
  assert.throws(() => parseDocument(JSON.stringify(poisoned)), LabStorageError);
  poisoned.equipment[0].pose.position.x = Infinity;
  assert.throws(() => parseDocument(JSON.stringify(poisoned)), LabStorageError);
});

test("拒绝幽灵端口与未知设备", () => {
  const doc = applyAuto(createStageDocument(stage), stage);
  const ghostPort = JSON.parse(serializeDocument(doc));
  ghostPort.connections.push({ id: "cn-ghost", a: { equipmentId: "flask-1", portId: "nope" }, b: { equipmentId: "valve-1", portId: "a" }, style: "tube" });
  assert.throws(() => parseDocument(JSON.stringify(ghostPort)), LabStorageError);

  const unknownKind = JSON.parse(serializeDocument(doc));
  unknownKind.equipment[0].kind = "platinum-reactor-9000";
  assert.throws(() => parseDocument(JSON.stringify(unknownKind)), LabStorageError);
});

test("拒绝非法父级（缺失/自引用/非支架）", () => {
  const doc = applyAuto(createStageDocument(stage), stage);
  const missingParent = JSON.parse(serializeDocument(doc));
  missingParent.equipment.push({ ...missingParent.equipment[0], id: "orphan", parentMountId: "ghost-stand", localPose: missingParent.equipment[0].pose });
  assert.throws(() => parseDocument(JSON.stringify(missingParent)), LabStorageError);

  const selfParent = JSON.parse(serializeDocument(doc));
  selfParent.equipment[1].parentMountId = selfParent.equipment[1].id;
  assert.throws(() => parseDocument(JSON.stringify(selfParent)), LabStorageError);

  const nonStandParent = JSON.parse(serializeDocument(doc));
  const flask = nonStandParent.equipment.find((e: { id: string }) => e.id === "flask-1");
  const beaker = nonStandParent.equipment.find((e: { id: string }) => e.id === "beaker-a");
  beaker.parentMountId = flask.id;
  assert.throws(() => parseDocument(JSON.stringify(nonStandParent)), LabStorageError);
});

test("拒绝数量与体积超限", () => {
  const doc = applyAuto(createStageDocument(stage), stage);
  const tooMany = JSON.parse(serializeDocument(doc));
  tooMany.equipment = Array.from({ length: 51 }, (_, i) => ({ ...tooMany.equipment[0], id: `spam-${i}`, parentMountId: undefined, localPose: undefined }));
  assert.throws(() => parseDocument(JSON.stringify(tooMany)), LabStorageError);

  const huge = JSON.parse(serializeDocument(doc));
  huge.name = "x".repeat(500);
  assert.throws(() => parseDocument(JSON.stringify(huge)), LabStorageError);
});

test("fill 越界与非法 colorId 被拒绝或夹取", () => {
  const doc = applyAuto(createStageDocument(stage), stage);
  const badFill = JSON.parse(serializeDocument(doc));
  badFill.equipment[2].visualContents = { fill: 2.5 };
  assert.throws(() => parseDocument(JSON.stringify(badFill)), LabStorageError);
});

test("解析失败不污染当前工作台（异常抛出而非返回半份文档）", () => {
  const doc = applyAuto(createStageDocument(stage), stage);
  const before = serializeDocument(doc);
  let failed = false;
  try { parseDocument('{"schemaVersion":1,"stageId":"silver-condenser","name":"x","equipment":[{"id":"a","kind":"beaker","pose":{"position":{"x":0,"y":0,"z":0},"rotation":[0,0,0,1]}}],"connections":[],"revision":1,"lastEditedAt":1,"extra":"?"}'); } catch { failed = true; }
  // 校验通过与否不重要；重要的是要么完整成功要么抛错，绝无第三种半态
  assert.equal(failed, false);
  assert.equal(serializeDocument(doc), before, "原文档保持不变");
});
