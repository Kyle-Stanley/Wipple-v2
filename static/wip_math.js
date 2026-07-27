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
    if (!has("C") && has("D") && has("Q")) out.C = out.D + out.Q;

    // Dollar anchors may close the progress side in either direction. A
    // printed percentage is deliberately never used to reverse-engineer
    // dollars: schedules commonly round or truncate it.
    if (!has("D") && has("C") && has("Q")) out.D = out.C - out.Q;
    if (!has("D") && has("E") && has("H")) out.D = out.E - out.H;
    if (!has("E") && has("D") && has("H")) out.E = out.D + out.H;
    if (!has("D") && has("E") && has("C") && has("V") && out.V !== 0)
      out.D = out.E * out.C / out.V;
    if (!has("P") && has("D") && has("C") && out.C !== 0) out.P = out.D / out.C;
    if (!has("Q") && has("C") && has("D")) out.Q = out.C - out.D;
    if (!has("E") && has("D") && has("C") && has("V") && out.V !== 0)
      out.E = out.D / out.C * out.V;

    // Under/over columns are stored as positive magnitudes. Together they
    // represent the signed net billing position O - U.
    if (!has("N") && has("U") && has("O")) out.N = Math.abs(out.O) - Math.abs(out.U);
    if (!has("B") && has("E") && has("N")) out.B = out.E + out.N;
    if (!has("B") && has("E") && has("U") && has("O"))
      out.B = out.E + Math.abs(out.O) - Math.abs(out.U);
    if (!has("B") && has("V") && has("RB")) out.B = out.V - out.RB;

    if (!has("V") && has("E") && has("R")) out.V = out.E + out.R;
    if (!has("E") && has("V") && has("R")) out.E = out.V - out.R;
    if (!has("V") && has("B") && has("RB")) out.V = out.B + out.RB;
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
    C: "Matches the independently implied estimated cost",
    G: "Matches contract value − estimated cost",
    D: "Matches the independently implied costs to date",
    Q: "Matches estimated cost − costs to date",
    E: "Matches the independently implied earned revenue",
    B: "Matches the independently implied billings",
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

  function allowedCorroborationMisses(count) {
    // Tiny samples cannot safely absorb misses: one coincidental relationship
    // can otherwise make a column ambiguous. Once there are at least ten rows,
    // allow two poisoned inputs; larger schedules may tolerate roughly 12%,
    // capped at four misses.
    return Math.min(4, Math.max(count >= 10 ? 2 : 0, Math.floor(count * 0.12)));
  }

  function corroborationStats(actualRows, derivedRows, variable) {
    const comparable = actualRows.map(({ value, index }) => ({
      actual: variable === "U" || variable === "O" ? Math.abs(value) : value,
      expected: +derivedRows[index]?.[variable],
    })).filter(({ expected }) => Number.isFinite(expected));
    const informative = comparable.filter(({ actual: observed, expected }) => {
      const tolerance = corroborationTolerance(observed, expected);
      return Math.abs(observed) > tolerance || Math.abs(expected) > tolerance;
    });
    if (informative.length < 3) return null;
    const matchedRows = informative.filter(({ actual: observed, expected }) =>
      Math.abs(observed - expected) <= corroborationTolerance(observed, expected)).length;
    const allowedMisses = allowedCorroborationMisses(informative.length);
    const requiredRows = Math.max(3, informative.length - allowedMisses);
    if (matchedRows < requiredRows) return null;
    return {
      matchedRows,
      comparedRows: informative.length,
      mismatches: informative.length - matchedRows,
    };
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
      const actualRows = (rows || []).map((row, index) => {
        const raw = row[column];
        const value = raw === null || raw === "" ? NaN : +raw;
        return { value, index };
      }).filter(({ value }) => Number.isFinite(value));
      if (actualRows.length < 3) continue;

      const matches = [];
      for (const variable of CORROBORATION_VARS) {
        if (usedVariables.has(variable)) continue;
        const stats = corroborationStats(actualRows, derivedRows, variable);
        if (stats) matches.push({ variable, ...stats });
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
        confirmed: false,
      };
    });

    // A manually/header-mapped physical column may also be independently
    // checkable. Remove that one column from the inputs before predicting it so
    // the check can never certify a value with itself.
    anchors.forEach(([columnText, variable]) => {
      const column = +columnText;
      const otherAnchors = anchors.filter(([other]) => +other !== column);
      const otherDerived = (rows || []).map((row) => {
        const printed = {};
        otherAnchors.forEach(([otherColumn, otherVariable]) => {
          const raw = row[+otherColumn];
          const value = raw === null || raw === "" ? NaN : +raw;
          if (Number.isFinite(value)) printed[otherVariable] = value;
        });
        return deriveCanonicalVars(printed);
      });
      const actualRows = (rows || []).map((row, index) => {
        const raw = row[column];
        const value = raw === null || raw === "" ? NaN : +raw;
        return { value, index };
      }).filter(({ value }) => Number.isFinite(value));
      const stats = corroborationStats(actualRows, otherDerived, variable);
      if (!stats) return;
      const rowNote = stats.mismatches
        ? ` · ${stats.matchedRows} of ${stats.comparedRows} rows`
        : ` · all ${stats.comparedRows} rows`;
      inferred[column] = {
        variable,
        reason: (CORROBORATION_REASONS[variable] || "Matches the calculated value") + rowNote,
        rows: (rows || []).length,
        ...stats,
        confirmed: true,
      };
    });

    return inferred;
  }

  const FIXED_MAPPING_RULES = [
    { id: "estimated-profit", label: "Contract value = estimated cost + estimated profit",
      output: "G", variables: ["V", "C", "G"], expected: (v) => v.V - v.C, kind: "money" },
    { id: "earned-revenue", label: "Earned revenue = contract value × cost to date ÷ estimated cost",
      output: "E", variables: ["V", "C", "D", "E"], expected: (v) => v.C ? v.V * v.D / v.C : NaN, kind: "money" },
    { id: "cost-to-complete", label: "Estimated cost = cost to date + cost to complete",
      output: "Q", variables: ["C", "D", "Q"], expected: (v) => v.C - v.D, kind: "money" },
    { id: "earned-profit", label: "Earned gross profit = earned revenue − cost to date",
      output: "H", variables: ["E", "D", "H"], expected: (v) => v.E - v.D, kind: "money" },
    { id: "net-billing", label: "Net billing position = billings − earned revenue",
      output: "N", variables: ["E", "B", "N"], expected: (v) => v.B - v.E, kind: "money" },
    { id: "underbillings", label: "Underbillings = max(earned revenue − billings, 0)",
      output: "U", variables: ["E", "B", "U"], expected: (v) => Math.max(v.E - v.B, 0), kind: "money", magnitude: true },
    { id: "overbillings", label: "Overbillings = max(billings − earned revenue, 0)",
      output: "O", variables: ["E", "B", "O"], expected: (v) => Math.max(v.B - v.E, 0), kind: "money", magnitude: true },
    { id: "remaining-revenue", label: "Remaining revenue = contract value − earned revenue",
      output: "R", variables: ["V", "E", "R"], expected: (v) => v.V - v.E, kind: "money" },
    { id: "remaining-billings", label: "Remaining billings = contract value − billings",
      output: "RB", variables: ["V", "B", "RB"], expected: (v) => v.V - v.B, kind: "money" },
    { id: "gross-margin", label: "Gross margin % = estimated profit ÷ contract value",
      output: "M", variables: ["V", "G", "M"], expected: (v) => v.V ? v.G / v.V : NaN, kind: "pct" },
    { id: "percent-complete-cost", label: "Percent complete = cost to date ÷ estimated cost",
      output: "P", variables: ["C", "D", "P"], expected: (v) => v.C ? v.D / v.C : NaN, kind: "pct" },
    { id: "percent-complete-revenue", label: "Percent complete = earned revenue ÷ contract value",
      output: "P", variables: ["V", "E", "P"], expected: (v) => v.V ? v.E / v.V : NaN, kind: "pct" },
    { id: "percent-billed", label: "Percent billed = billings ÷ contract value",
      output: "PB", variables: ["V", "B", "PB"], expected: (v) => v.V ? v.B / v.V : NaN, kind: "pct" },
  ];

  function fixedAuditTolerance(observed, expected, kind) {
    if (kind === "pct") return Math.max(0.002, 1e-9 * Math.abs(expected));
    return corroborationTolerance(observed, expected);
  }

  function auditFixedMapping(rows, mapping, labels = []) {
    const columnByVariable = {};
    Object.entries(mapping || {}).forEach(([column, variable]) => {
      if (variable && columnByVariable[variable] == null)
        columnByVariable[variable] = +column;
    });
    const available = FIXED_MAPPING_RULES.filter((rule) =>
      rule.variables.every((variable) => columnByVariable[variable] != null));
    const relations = [];
    const failedByRow = new Map();
    const checkedJobs = new Set();

    for (const rule of available) {
      const failures = [];
      let checkedRows = 0;
      (rows || []).forEach((row, rowIndex) => {
        const values = {};
        for (const variable of rule.variables) {
          const raw = row?.[columnByVariable[variable]];
          const value = raw === null || raw === "" ? NaN : +raw;
          if (!Number.isFinite(value)) return;
          values[variable] = value;
        }
        const expected = +rule.expected(values);
        if (!Number.isFinite(expected)) return;
        const printed = values[rule.output];
        const observed = rule.magnitude ? Math.abs(printed) : printed;
        const tolerance = fixedAuditTolerance(observed, expected, rule.kind);
        checkedRows += 1;
        checkedJobs.add(rowIndex);
        if (Math.abs(observed - expected) <= tolerance) return;

        const failure = {
          rowIndex,
          rowLabel: String(labels[rowIndex] || `Row ${rowIndex + 1}`),
          relation: rule.label,
          relationId: rule.id,
          variables: [...rule.variables],
          outputVariable: rule.output,
          observed: printed,
          expected,
          difference: observed - expected,
          tolerance,
        };
        failures.push(failure);
        const grouped = failedByRow.get(rowIndex) || {
          rowIndex,
          rowLabel: failure.rowLabel,
          relations: [],
          variables: new Set(),
          details: [],
        };
        grouped.relations.push(rule.label);
        rule.variables.forEach((variable) => grouped.variables.add(variable));
        grouped.details.push(failure);
        failedByRow.set(rowIndex, grouped);
      });
      if (checkedRows >= 3)
        relations.push({ id: rule.id, label: rule.label, checkedRows, failures });
    }

    const retained = new Set(relations.map((relation) => relation.id));
    const failedRows = [...failedByRow.values()].map((failure) => ({
      ...failure,
      relations: failure.relations.filter((_, index) =>
        retained.has(failure.details[index]?.relationId)),
      details: failure.details.filter((detail) => retained.has(detail.relationId)),
      variables: [...failure.variables],
    })).filter((failure) => failure.details.length);

    return {
      relations,
      failedRows,
      checkedRows: checkedJobs.size,
      passed: relations.length > 0 && failedRows.length === 0,
      limited: true,
    };
  }

  return {
    deriveCanonicalVars,
    mappingReadiness,
    inferCorroboratingColumns,
    auditFixedMapping,
  };
});
