(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.WippleMath = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function deriveCanonicalVars(values) {
    const out = { ...values };
    const has = (key) => Number.isFinite(+out[key]);

    if (!has("G") && has("V") && has("C")) out.G = out.V - out.C;
    if (!has("P") && has("D") && has("C") && out.C !== 0) out.P = out.D / out.C;
    if (!has("Q") && has("C") && has("D")) out.Q = out.C - out.D;
    if (!has("R") && has("V") && has("E")) out.R = out.V - out.E;
    if (!has("RB") && has("V") && has("B")) out.RB = out.V - out.B;
    if (!has("M") && has("G") && has("V") && out.V !== 0) out.M = out.G / out.V;
    if (!has("PB") && has("B") && has("V") && out.V !== 0) out.PB = out.B / out.V;
    if (!has("H") && has("E") && has("D")) out.H = out.E - out.D;
    if (has("E") && has("B")) {
      if (!has("U")) out.U = Math.max(out.E - out.B, 0);
      if (!has("O")) out.O = Math.max(out.B - out.E, 0);
      if (!has("N")) out.N = out.B - out.E;
    }
    return out;
  }

  return { deriveCanonicalVars };
});
