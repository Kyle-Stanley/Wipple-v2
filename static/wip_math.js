(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else {
    root.WippleMath = api;
    // Keep the mapping-page presentation isolated from the accounting helpers.
    // index.html already loads this module, so this small browser-only loader lets
    // the UI enhancement remain a separate static asset.
    if (typeof document !== "undefined" && !document.querySelector("script[data-wipple-mapping-ui]")) {
      const script = document.createElement("script");
      script.src = "/static/mapping_ui.js";
      script.dataset.wippleMappingUi = "true";
      document.head.appendChild(script);
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function deriveCanonicalVars(values) {
    const out = { ...values };
    const has = (key) => Number.isFinite(+out[key]);

    if (!has("V") && has("C") && has("G")) out.V = out.C + out.G;
    if (!has("C") && has("V") && has("G")) out.C = out.V - out.G;
    if (!has("G") && has("V") && has("C")) out.G = out.V - out.C;

    // Dollar anchors may close the progress side in either direction. A
    // printed percentage is deliberately never used to reverse-engineer
    // dollars: schedules commonly round or truncate it.
    if (!has("D") && has("C") && has("Q")) out.D = out.C - out.Q;
    if (!has("D") && has("E") && has("C") && has("V") && out.V !== 0)
      out.D = out.E * out.C / out.V;
    if (!has("P") && has("D") && has("C") && out.C !== 0) out.P = out.D / out.C;
    if (!has("Q") && has("C") && has("D")) out.Q = out.C - out.D;
    if (!has("E") && has("D") && has("C") && has("V") && out.C !== 0)
      out.E = out.D / out.C * out.V;

    // Under/over columns are stored as positive magnitudes. Together they
    // represent the signed net billing position O - U.
    if (!has("N") && has("U") && has("O")) out.N = Math.abs(out.O) - Math.abs(out.U);
    if (!has("B") && has("E") && has("N")) out.B = out.E + out.N;
    if (!has("B") && has("E") && has("U") && has("O"))
      out.B = out.E + Math.abs(out.O) - Math.abs(out.U);

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

  function mappingReadiness(variables) {
    const present = new Set(Array.isArray(variables)
      ? variables
      : Object.keys(variables || {}).filter((key) => variables[key]));
    const profitVars = ["V", "C", "G"].filter((key) => present.has(key));
    const progressVars = ["D", "Q", "E"].filter((key) => present.has(key));
    const billingVars = ["B", "N", "U", "O"].filter((key) => present.has(key));
    const profitability = profitVars.length >= 2;
    const progress = progressVars.length >= 1;
    const billing = present.has("B") || present.has("N")
      || (present.has("U") && present.has("O"));
    const groups = [
      { id: "profitability", complete: profitability, variables: profitVars },
      { id: "progress", complete: progress, variables: progressVars },
      { id: "billing", complete: billing, variables: billingVars },
    ];
    const score = groups.filter((group) => group.complete).length;
    return { score, total: groups.length, complete: score === groups.length, groups };
  }

  const CORROBORATION_VARS = [
    "V", "C", "G", "D", "Q", "E", "B", "H", "N", "U", "O", "R", "RB",
  ];
  const CORROBORATION_REASONS = {
    V: "Matches estimated cost + estimated profit",
    C: "Matches contract value − estimated profit",
    G: "Matches contract value − estimated cost",
    D: "Matches calculated costs to date",
    Q: "Matches estimated cost − costs to date",
    E: "Matches calculated earned revenue",
    B: "Matches earned revenue + billing position",
    H: "Matches earned revenue − costs to date",
    N: "Matches billings − earned revenue",
    U: "Matches calculated underbillings",
    O: "Matches calculated overbillings",
    R: "Matches contract value − earned revenue",
    RB: "Matches contract value − billings",
  };

  function corroborationTolerance(observed, expected) {
    // Additive identities should normally agree to the dollar. Multiplication
    // and division can leave a small rounding residue, so permit two cents per
    // $1,000 (0.002%) while retaining the existing ~$2 absolute floor.
    const scale = Math.max(Math.abs(observed), Math.abs(expected), 1);
    return Math.max(2.05, scale * 0.00002);
  }

  function inferCorroboratingColumns(rows, mapping, ignoredColumns = []) {
    const anchors = Object.entries(mapping || {})
      .filter(([, variable]) => variable);
    if (!mappingReadiness(anchors.map(([, variable]) => variable)).complete)
      return {};

    const ignored = new Set(Array.from(ignoredColumns || [], Number));
    const usedVariables = new Set(anchors.map(([, variable]) => variable));
    const usedColumns = new Set(anchors.map(([column]) => +column));
    const derivedRows = (rows || []).map((row) => {
      const printed = {};
      anchors.forEach(([column, variable]) => {
        const raw = row[+column];
        const value = raw === null || raw === "" ? NaN : +raw;
        if (Number.isFinite(value)) printed[variable] = value;
      });
      return deriveCanonicalVars(printed);
    });
    const width = (rows || []).reduce((largest, row) =>
      Math.max(largest, Array.isArray(row) ? row.length : 0), 0);
    const candidatesByColumn = new Map();

    for (let column = 0; column < width; column += 1) {
      if (usedColumns.has(column) || ignored.has(column)) continue;
      const actual = (rows || []).map((row) => {
        const raw = row[column];
        return raw === null || raw === "" ? NaN : +raw;
      });
      const actualRows = actual.map((value, index) => ({ value, index }))
        .filter(({ value }) => Number.isFinite(value));
      if (actualRows.length < 3) continue;

      const matches = [];
      for (const variable of CORROBORATION_VARS) {
        if (usedVariables.has(variable)) continue;
        const comparable = actualRows.map(({ value, index }) => ({
          actual: variable === "U" || variable === "O" ? Math.abs(value) : value,
          expected: +derivedRows[index][variable],
        })).filter(({ expected }) => Number.isFinite(expected));
        const informative = comparable.filter(({ actual: observed, expected }) => {
          const tolerance = corroborationTolerance(observed, expected);
          return Math.abs(observed) > tolerance || Math.abs(expected) > tolerance;
        });
        if (informative.length < 3) continue;

        const matchedRows = informative.filter(({ actual: observed, expected }) =>
          Math.abs(observed - expected) <= corroborationTolerance(observed, expected)).length;
        const allowedMisses = Math.min(2, Math.floor(informative.length * 0.2));
        const requiredRows = Math.max(3, informative.length - allowedMisses);
        if (matchedRows >= requiredRows) {
          matches.push({
            variable,
            matchedRows,
            comparedRows: informative.length,
            mismatches: informative.length - matchedRows,
          });
        }
      }
      if (matches.length === 1) candidatesByColumn.set(column, matches[0]);
    }

    const columnsByVariable = new Map();
    candidatesByColumn.forEach((match, column) => {
      const columns = columnsByVariable.get(match.variable) || [];
      columns.push(column);
      columnsByVariable.set(match.variable, columns);
    });
    const inferred = {};
    candidatesByColumn.forEach((match, column) => {
      if (columnsByVariable.get(match.variable).length !== 1) return;
      const rowNote = match.mismatches
        ? ` · ${match.matchedRows} of ${match.comparedRows} rows`
        : ` · all ${match.comparedRows} rows`;
      inferred[column] = {
        variable: match.variable,
        reason: (CORROBORATION_REASONS[match.variable] || "Matches the calculated value") + rowNote,
        rows: (rows || []).length,
        matchedRows: match.matchedRows,
        comparedRows: match.comparedRows,
        mismatches: match.mismatches,
      };
    });
    return inferred;
  }

  return { deriveCanonicalVars, mappingReadiness, inferCorroboratingColumns };
});
