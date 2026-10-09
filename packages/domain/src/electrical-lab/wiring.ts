import { validateElectricalLabDocument } from "./document.ts";
import type { ElectricalComponent, ElectricalLabDocument, Point } from "./model.ts";

export function sameCircuitPoint(a: Point, b: Point): boolean { return Math.abs(a.x - b.x) < .001 && Math.abs(a.y - b.y) < .001; }

/** Every displayed segment is axial, including imported bends and rotated leads. */
export function routeOrthogonal(from: Point, to: Point, bends: Point[] = [], fromDirection: Point | null = null, toDirection: Point | null = null): Point[] {
  const stub = (point: Point, direction: Point) => ({ x: point.x + direction.x * 16, y: point.y + direction.y * 16 });
  const anchors = [from, ...(fromDirection ? [stub(from, fromDirection)] : []), ...bends, ...(toDirection ? [stub(to, toDirection)] : []), to];
  const result: Point[] = [from];
  for (const target of anchors.slice(1)) {
    const previous = result.at(-1)!;
    if (sameCircuitPoint(previous, target)) continue;
    if (Math.abs(previous.x - target.x) > .001 && Math.abs(previous.y - target.y) > .001) {
      const horizontal = Math.abs(target.x - previous.x) >= Math.abs(target.y - previous.y);
      const middle = Math.round((horizontal ? previous.x + target.x : previous.y + target.y) / 20) * 10;
      result.push(...(horizontal ? [{ x: middle, y: previous.y }, { x: middle, y: target.y }] : [{ x: previous.x, y: middle }, { x: target.x, y: middle }]));
    }
    result.push(target);
  }
  const simplified: Point[] = [];
  for (const point of result) {
    if (simplified.length && sameCircuitPoint(simplified.at(-1)!, point)) continue;
    while (simplified.length >= 2) {
      const a = simplified.at(-2)!, b = simplified.at(-1)!;
      const horizontal = Math.abs(a.y - b.y) < .001 && Math.abs(b.y - point.y) < .001 && (b.x - a.x) * (point.x - b.x) >= 0;
      const vertical = Math.abs(a.x - b.x) < .001 && Math.abs(b.x - point.x) < .001 && (b.y - a.y) * (point.y - b.y) >= 0;
      if (!horizontal && !vertical) break;
      simplified.pop();
    }
    simplified.push(point);
  }
  return simplified;
}

/** Snap only along the clicked segment; never move a branch off its parent wire. */
export function projectOnWirePath(points: Point[], point: Point, grid = 10): { point: Point; segment: number; distance: number } | null {
  let nearest: { point: Point; segment: number; distance: number } | null = null;
  for (let index = 1; index < points.length; index++) {
    const a = points[index - 1]!, b = points[index]!;
    const horizontal = Math.abs(a.y - b.y) < .001;
    const value = horizontal ? point.x : point.y;
    const min = horizontal ? Math.min(a.x, b.x) : Math.min(a.y, b.y);
    const max = horizontal ? Math.max(a.x, b.x) : Math.max(a.y, b.y);
    const snapped = Math.max(min, Math.min(max, Math.round(value / grid) * grid));
    const projected = horizontal ? { x: snapped, y: a.y } : { x: a.x, y: snapped };
    const distance = Math.hypot(projected.x - point.x, projected.y - point.y);
    if (!nearest || distance < nearest.distance) nearest = { point: projected, segment: index, distance };
  }
  return nearest;
}

/** Insert an electrical junction without changing the old wire's geometry or net. */
export function spliceElectricalWire(document: ElectricalLabDocument, wireId: string, node: ElectricalComponent, path: Point[]): ElectricalLabDocument {
  const wire = document.wires.find(item => item.id === wireId);
  const hit = projectOnWirePath(path, node, 1e-6);
  if (!wire || node.kind !== "junction" || !hit || hit.distance > .001 || sameCircuitPoint(node, path[0]!) || sameCircuitPoint(node, path.at(-1)!)) throw new Error("invalid_junction");
  const ref = `${node.id}:p`;
  let secondId = `${node.id}_wire`, suffix = 1;
  while (document.wires.some(item => item.id === secondId) || document.components.some(item => item.id === secondId)) secondId = `${node.id}_wire_${suffix++}`;
  const first = { ...wire, to: ref, bends: path.slice(1, hit.segment).filter(point => !sameCircuitPoint(point, node)) };
  const second = { ...wire, id: secondId, from: ref, bends: path.slice(hit.segment, -1).filter(point => !sameCircuitPoint(point, node)) };
  return validateElectricalLabDocument({ ...document, components: [...document.components, node], wires: document.wires.flatMap(item => item.id === wireId ? [first, second] : [item]) });
}
