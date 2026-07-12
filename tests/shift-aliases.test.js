const assert = require("node:assert/strict");
const aliases = require("../shift-aliases.js");

assert.deepEqual(
  aliases.parseAliasInput(" D, day,\n데이 , D "),
  ["D", "DAY", "데이"],
  "comma/newline separated aliases should be normalized and deduplicated"
);

assert.equal(
  aliases.normalizeAliasToken("  off  "),
  "OFF",
  "single alias tokens should trim and uppercase"
);

const normalized = aliases.normalizeAliasConfig({
  day: ["d", "DAY"],
  off: ["휴무", "off", "휴무"],
});

assert.deepEqual(normalized.day, ["D", "DAY"]);
assert.deepEqual(normalized.off, ["휴무", "OFF"]);
assert.deepEqual(normalized.night, ["N", "NN", "NIGHT", "나이트"]);

console.log("shift-aliases tests passed");
