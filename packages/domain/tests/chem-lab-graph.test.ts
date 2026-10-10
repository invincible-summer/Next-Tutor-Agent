/**
 * 连接图测试：端口占用、多段/分支/闭环、内部通路随开关变化。
 * 只验证图数据完整性与演出可达性，不验证化学正确性。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  applyLabAction, connectionBlockReason, connectionsAtPort, createStageDocument,
  getStage, portOccupied, portsCompatible, reachableCueEdges, getEquipmentSpec,
} from "../src/chem-lab/index.ts";
import type { LabDocument, PortRef } from "../src/chem-lab/index.ts";

const stage = getStage("chromatic-loop")!;

function docWithLoop(): LabDocument {
  return createStageDocument(stage);
}

function ref(equipmentId: string, portId: string): PortRef { return { equipmentId, portId }; }

test("端口占用与释放", () => {
  let doc = createStageDocument(getStage("bubble-relay")!);
  doc = applyLabAction(doc, { type: "replaceAuto" }, getStage("bubble-relay")!).document;
  // flask-1.neck 已被 cn-1 占用
  assert.ok(portOccupied(doc, ref("flask-1", "neck")));
  assert.ok(connectionsAtPort(doc, ref("flask-1", "neck")).length === 1);
  const busy = applyLabAction(doc, { type: "connect", a: ref("flask-1", "neck"), b: ref("collect-1", "vent") }, getStage("bubble-relay")!);
  assert.equal(busy.ok, false);
  assert.equal(busy.hint, "port_busy_or_missing");
  // 拆掉后可再接
  const cut = applyLabAction(doc, { type: "disconnect", connectionId: "cn-1" }, getStage("bubble-relay")!);
  assert.ok(cut.ok);
  assert.equal(portOccupied(cut.document, ref("flask-1", "neck")), false);
});

test("connect 拒绝幽灵端口、同一端口与 liquid↔liquid", () => {
  let doc = createStageDocument(getStage("bubble-relay")!);
  doc = applyLabAction(doc, { type: "add", kind: "beaker", pose: { position: { x: 6, y: 0, z: 2 }, rotation: [0, 0, 0, 1] } }, getStage("bubble-relay")!).document;
  doc = applyLabAction(doc, { type: "add", kind: "beaker", pose: { position: { x: 6, y: 0, z: 3 }, rotation: [0, 0, 0, 1] } }, getStage("bubble-relay")!).document;
  const beakers = doc.equipment.filter(e => e.kind === "beaker");
  const a = beakers[0]!, b = beakers[1]!;
  assert.equal(connectionBlockReason(doc, ref(a.id, "nope"), ref(b.id, "rim")), "port_missing");
  assert.equal(connectionBlockReason(doc, ref(a.id, "rim"), ref(a.id, "rim")), "same_port");
  assert.equal(connectionBlockReason(doc, ref(a.id, "rim"), ref(b.id, "rim")), "not_connectable");
  // 玻璃管口可以卸料到开口容器沿
  assert.equal(connectionBlockReason(doc, ref("u-1", "out"), ref(a.id, "rim")), null);
});

test("socket 兼容表：开口容器沿只作接收端", () => {
  const liquid = getEquipmentSpec("beaker")!.ports[0]!;
  const joint = getEquipmentSpec("flask-round")!.ports[0]!;
  const gas = getEquipmentSpec("washing-bottle")!.ports[0]!;
  assert.equal(portsCompatible(joint, liquid), true);
  assert.equal(portsCompatible(liquid, gas), true);
  assert.equal(portsCompatible(liquid, liquid), false);
  assert.equal(portsCompatible(joint, gas), true);
});

test("闭环遍历不死循环且覆盖全部环上连线", () => {
  let doc = applyLabAction(docWithLoop(), { type: "replaceAuto" }, stage).document;
  // 打开两阀、以运行中泵的进出口为源（演出层约定：泵开时进出口都算驱动源）
  doc = applyLabAction(doc, { type: "setControl", id: "valve-a", controlId: "open", value: true }, stage).document;
  doc = applyLabAction(doc, { type: "setControl", id: "valve-b", controlId: "open", value: true }, stage).document;
  const { edges, directions } = reachableCueEdges(doc, [ref("pump-1", "in"), ref("pump-1", "out")]);
  const loopConnections = ["cn-1", "cn-2", "cn-3", "cn-4", "cn-5", "cn-6", "cn-7", "cn-8", "cn-9", "cn-10"];
  for (const id of loopConnections) assert.ok(edges.has(id), `闭环连线 ${id} 应可达`);
  assert.ok(directions.size > 0);
  // 闭环存在（res-1.return 回到起点）也不会死循环——测试本身完成即为证明
});

test("关阀截断该分支的演出通路", () => {
  let doc = applyLabAction(docWithLoop(), { type: "replaceAuto" }, stage).document;
  // 两阀全关（模板默认即关）：流动只到两个阀的进水侧
  const closed = reachableCueEdges(doc, [ref("pump-1", "out")]);
  assert.ok(closed.edges.has("cn-2") && closed.edges.has("cn-3") && closed.edges.has("cn-6"), "主流与阀前管段应可见流动");
  assert.equal(closed.edges.has("cn-4"), false, "A 支路阀后应静止");
  assert.equal(closed.edges.has("cn-8"), false, "B 支路阀后应静止");
  // 打开 A 阀
  doc = applyLabAction(doc, { type: "setControl", id: "valve-a", controlId: "open", value: true }, stage).document;
  const openA = reachableCueEdges(doc, [ref("pump-1", "out")]);
  assert.ok(openA.edges.has("cn-3") && openA.edges.has("cn-4"), "A 支路应可达");
  assert.equal(openA.edges.has("cn-7"), false, "B 阀出口侧管段仍应静止");
  // 打开 B 阀后两支都通
  doc = applyLabAction(doc, { type: "setControl", id: "valve-b", controlId: "open", value: true }, stage).document;
  const openBoth = reachableCueEdges(doc, [ref("pump-1", "out")]);
  assert.ok(openBoth.edges.has("cn-7") && openBoth.edges.has("cn-8"));
});

test("三通/四通分支支持多路同时传播", () => {
  const doc = applyLabAction(createStageDocument(getStage("pulse-theatre")!), { type: "replaceAuto" }, getStage("pulse-theatre")!).document;
  const { edges } = reachableCueEdges(doc, [ref("pump-1", "out")]);
  // 主路 + 分支（阀门默认关闭时只到主阀为止）
  assert.ok(edges.has("cn-2"));
  assert.equal(edges.has("cn-4"), false, "主阀关闭时观察球支路不通");
  assert.equal(edges.has("cn-8"), false, "旁路阀关闭时 u 管支路不通");
});
