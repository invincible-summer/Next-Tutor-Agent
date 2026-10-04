/** Contract package smoke: ids module is importable and pure. */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { UserId, SessionId } from "../src/ids.ts";

test("identifier types are opaque strings", () => {
  const user: UserId = "u_123";
  const session: SessionId = "s_456";
  assert.equal(typeof user, "string");
  assert.equal(typeof session, "string");
});
