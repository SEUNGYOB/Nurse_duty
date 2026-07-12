(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
    return;
  }
  root.DutyShiftAliases = factory();
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  var SHIFT_KEYS = ["day", "evening", "night", "s", "annual", "off"];

  function defaultAliasConfig() {
    return {
      day: ["D", "DAY", "데이"],
      evening: ["E", "EVE", "EVENING", "이브닝"],
      night: ["N", "NN", "NIGHT", "나이트"],
      s: ["S"],
      annual: ["A", "ANNUAL", "연차"],
      off: ["O", "OFF", "휴무"],
    };
  }

  function normalizeAliasToken(value) {
    return String(value || "")
      .trim()
      .replace(/\s+/g, " ")
      .toUpperCase();
  }

  function uniqueAliases(values) {
    var seen = new Set();
    var result = [];
    values.forEach(function (value) {
      var normalized = normalizeAliasToken(value);
      if (!normalized || seen.has(normalized)) return;
      seen.add(normalized);
      result.push(normalized);
    });
    return result;
  }

  function parseAliasInput(value) {
    return uniqueAliases(String(value || "").split(/[,\n]/));
  }

  function normalizeAliasConfig(candidate) {
    var defaults = defaultAliasConfig();
    var next = {};
    SHIFT_KEYS.forEach(function (shiftKey) {
      var values = candidate && Array.isArray(candidate[shiftKey]) ? candidate[shiftKey] : defaults[shiftKey];
      next[shiftKey] = uniqueAliases(values);
    });
    return next;
  }

  return {
    SHIFT_KEYS: SHIFT_KEYS,
    defaultAliasConfig: defaultAliasConfig,
    normalizeAliasToken: normalizeAliasToken,
    parseAliasInput: parseAliasInput,
    normalizeAliasConfig: normalizeAliasConfig,
  };
});
