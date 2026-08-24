import test from "node:test";
import assert from "node:assert/strict";

import { recordStateLabel } from "../src/constants/index.js";

test("recordStateLabel maps pending_review to an operator-facing label and leaves unknown states untouched", () => {
  assert.equal(recordStateLabel("pending_review"), "待人工复核");
  assert.equal(recordStateLabel("ready"), "已录入");
  assert.equal(recordStateLabel("field_missing"), "字段缺失");
  assert.equal(recordStateLabel("parse_failed"), "解析失败");
  assert.equal(recordStateLabel("postprocess_failed"), "处理失败");
  assert.equal(recordStateLabel("unrecognized_token"), "unrecognized_token");
});
