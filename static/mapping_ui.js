(function () {
  "use strict";

  const STYLE_ID = "wipple-mapping-ui-enhancements";
  const SUMMARY_CLASS = "mapping-inferred-summary";
  const PCT_VARS = new Set(["M", "P", "PB"]);
  const VAR_ORDER = ["V", "C", "G", "D", "Q", "E", "B", "H", "N", "U", "O", "R", "RB", "M", "P", "PB"];
  const VAR_NAMES = {
    V: "Contract Value", C: "Estimated Total Cost", G: "Estimated Gross Profit",
    D: "Cost to Date", Q: "Cost to Complete", E: "Earned Revenue",
    B: "Billings to Date", H: "Earned Gross Profit to Date",
    N: "Net Billing Position", U: "Underbillings", O: "Overbillings",
    R: "Remaining Revenue", RB: "Remaining Billings", M: "Gross Margin %",
    P: "Percent Complete", PB: "Percent Billed",
  };
  let refreshQueued = false;

  function installStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      #mapping{padding:12px 0 56px}
      .mapping-wide{width:min(1580px,calc(100vw - 88px));max-width:none}
      .mapping-head{margin-bottom:12px}
      .mapping-head h2{font-size:27px}
      .mapping-head p{margin-top:3px}
      .mapping-layout{grid-template-columns:minmax(0,1fr) 248px;gap:18px}
      .mapping-table-note{padding:8px 12px}
      .mapping-scroll{max-height:calc(100vh - 274px)}
      .mapping-grid{min-width:0;width:max-content}
      .mapping-grid th,.mapping-grid td{
        width:154px;min-width:146px;max-width:162px;padding:6px 8px;
        overflow:hidden;text-overflow:ellipsis;white-space:nowrap
      }
      .mapping-grid th{padding:8px;white-space:normal}
      .mapping-grid th:first-child,.mapping-grid td:first-child{
        width:104px;min-width:104px;max-width:104px
      }
      .mapping-grid th:nth-child(2),.mapping-grid td:nth-child(2){
        width:250px;min-width:250px;max-width:250px;text-align:left
      }
      .mapping-grid th:nth-child(4),.mapping-grid td:nth-child(4){
        width:170px;min-width:170px;max-width:178px
      }
      .mapping-grid td:nth-child(2){overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .mapping-doc-head{position:relative;min-height:27px;margin-bottom:5px;padding-right:22px}
      .mapping-select{min-height:30px;border-width:1.5px;font-weight:600}
      .mapping-select.suggested{background:#E7EDE4;border-color:#B9C8B4;color:#304B36}
      .mapping-select.user{background:#EEF1EA;border-color:var(--sage-deep);color:var(--sage-deep)}
      .mapping-select.inferred{background:#E7EFE5;border-color:#88A184;color:#294B31}
      .mapping-validation-note{display:none}
      .mapping-grid th.mapping-suggested{background:#F2F5EF;box-shadow:inset 0 3px 0 #A2B49D}
      .mapping-grid th.mapping-user{background:#EEF2EB;box-shadow:inset 0 3px 0 var(--sage-deep)}
      .mapping-grid th.mapping-inferred{background:#EDF3EA;box-shadow:inset 0 3px 0 #668165}
      .mapping-math-check{position:absolute;right:0;top:-1px;display:grid;place-items:center;width:17px;height:17px;border-radius:999px;background:var(--sage-deep);color:white;font-size:10px;font-weight:700;line-height:1}
      .mapping-rail{top:12px;padding:15px}
      .mapping-inferred-summary{margin:0 0 12px;padding:10px;border:1px solid #C5D2C1;border-radius:9px;background:#EEF3EB;color:#304B36}
      .mapping-inferred-summary>strong{display:block;font-size:10.5px;letter-spacing:.055em;text-transform:uppercase;margin-bottom:6px}
      .mapping-inferred-list{display:flex;flex-direction:column;gap:5px}
      .mapping-inferred-item{font-size:10.5px;line-height:1.35}
      .mapping-inferred-item b{display:block;font-size:11px;color:#294B31}
      .mapping-inferred-item span{color:#607461}
      .mapping-derived{margin-top:7px}

      #dash .banner.manual-audit-warning{display:flex;flex-direction:column;gap:2px;cursor:pointer;outline:none}
      #dash .banner.manual-audit-warning:hover{border-color:#C98274;background:#F7EBDD}
      #dash .banner.manual-audit-warning:focus-visible{box-shadow:0 0 0 3px rgba(163,64,47,.18)}
      #dash .banner.manual-audit-warning strong{display:block;color:var(--brick);font-weight:650}
      #dash .banner.manual-audit-warning span{display:block;color:var(--amber)}

      .manual-audit-details{max-width:900px;margin:16px auto 0;padding:14px 16px;border:1px solid #D9C28F;border-radius:10px;background:#F6F0E2;text-align:left}
      .manual-audit-details>header{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:10px}
      .manual-audit-details>header strong{display:block;font-size:14px;color:var(--amber)}
      .manual-audit-details>header p{font-size:11.5px;color:var(--muted);margin-top:3px;max-width:70ch;line-height:1.45}
      .manual-audit-list{display:flex;flex-direction:column;gap:9px}
      .manual-audit-row{border:1px solid var(--line-soft);border-radius:9px;background:var(--surface);padding:11px 12px}
      .manual-audit-row.edited{border-color:#AFC2AA}
      .manual-audit-row.focused{animation:manualfocus 1.4s ease-out}
      @keyframes manualfocus{0%{box-shadow:0 0 0 4px rgba(92,113,96,.24)}100%{box-shadow:0 0 0 0 transparent}}
      .manual-audit-row-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
      .manual-audit-row-head strong{display:block;font-size:13px;color:var(--ink)}
      .manual-audit-row-head small{display:block;font-size:10.5px;color:var(--muted);margin-top:2px}
      .manual-audit-row-head .btn{padding:4px 9px;font-size:10.5px;white-space:nowrap}
      .manual-edit-badge{display:inline-block;margin-left:6px;border-radius:999px;padding:1px 6px;background:#E4E9E1;color:var(--sage-deep);font-size:9px;font-weight:650;letter-spacing:.03em;text-transform:uppercase}
      .manual-audit-facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:6px;margin-top:9px}
      .manual-audit-fact{border:1px solid var(--line-soft);border-radius:7px;background:var(--paper);padding:7px 8px;min-width:0}
      .manual-audit-fact span{display:block;font-size:9.5px;color:var(--muted);line-height:1.25}
      .manual-audit-fact b{display:block;margin-top:2px;font-size:11.5px;font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .manual-audit-fact small{display:block;margin-top:2px;font-size:9px;color:var(--sage-deep)}
      .manual-audit-equations{display:flex;flex-direction:column;gap:5px;margin-top:8px}
      .manual-audit-equation{border-left:3px solid #D0A55B;padding:5px 8px;background:#FCF8EF;border-radius:0 6px 6px 0}
      .manual-audit-equation strong{display:block;font-size:11px;color:var(--brick)}
      .manual-audit-equation span{display:block;font-size:10.5px;color:var(--ink);margin-top:2px;line-height:1.35}
      .manual-audit-equation small{display:block;font-size:9.5px;color:var(--muted);margin-top:2px}
      .manual-edit-form{margin-top:10px;padding-top:10px;border-top:1px solid var(--line-soft)}
      .manual-edit-form.hidden{display:none}
      .manual-edit-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:8px}
      .manual-edit-field label{display:block;font-size:10px;font-weight:600;color:var(--ink);margin-bottom:3px}
      .manual-edit-field input{width:100%;border:1px solid var(--line);border-radius:7px;background:var(--surface);color:var(--ink);font:500 12px Inter,system-ui,sans-serif;padding:6px 8px;font-variant-numeric:tabular-nums}
      .manual-edit-field input:focus{outline:none;border-color:var(--sage-deep);box-shadow:0 0 0 2px rgba(92,113,96,.13)}
      .manual-edit-field small{display:block;font-size:9px;color:var(--muted);margin-top:3px}
      .manual-edit-actions{display:flex;align-items:center;justify-content:flex-end;gap:8px;margin-top:9px}
      .manual-edit-actions .btn{padding:5px 10px;font-size:10.5px}
      .manual-edit-status{margin-right:auto;font-size:10px;color:var(--brick)}
      .manual-edit-any{margin-top:10px;border-top:1px solid rgba(146,103,30,.18);padding-top:9px}
      .manual-edit-any summary{cursor:pointer;color:var(--sage-deep);font-size:10.5px;font-weight:600;list-style:none;display:inline-block;border-bottom:1px dotted var(--sage)}
      .manual-edit-any summary::-webkit-details-marker{display:none}
      .manual-edit-any-controls{display:flex;align-items:center;gap:8px;margin-top:8px}
      .manual-edit-any select{min-width:240px;max-width:420px;border:1px solid var(--line);border-radius:7px;background:var(--surface);color:var(--ink);font:500 11px Inter;padding:6px 8px}
      .manual-edit-any .btn{padding:5px 10px;font-size:10.5px}

      @media(max-width:900px){
        .mapping-wide{width:min(100%,calc(100vw - 32px))}
        .mapping-layout{grid-template-columns:1fr}
        .mapping-rail{position:static;grid-row:1}
        .mapping-scroll{max-height:60vh}
      }
      @media(max-width:620px){
        #mapping{padding-top:6px}
        .mapping-grid th:nth-child(2),.mapping-grid td:nth-child(2){width:205px;min-width:205px;max-width:205px}
        .manual-audit-details>header,.manual-audit-row-head{flex-direction:column}
        .manual-edit-any-controls{align-items:stretch;flex-direction:column}
        .manual-edit-any select{width:100%;min-width:0}
      }
    `;
    document.head.appendChild(style);
  }

  function selectedLabel(select) {
    const option = select.options[select.selectedIndex];
    return option ? option.textContent.trim().replace(/\s+·\s+reference only$/, "") : "";
  }

  function headerLabel(select) {
    return select.closest("th")?.querySelector(".mapping-doc-head")?.textContent.replace("✓", "").trim() || "Column";
  }

  function variableLabel(variable) {
    return VAR_NAMES[variable] || variable || "Value";
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"]/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
    })[char]);
  }

  function formatValue(value, variable) {
    if (!Number.isFinite(+value)) return "—";
    const number = +value;
    if (PCT_VARS.has(variable)) return `${(number * 100).toFixed(1)}%`;
    const abs = Math.abs(number).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    return number < 0 ? `($${abs})` : `$${abs}`;
  }

  function inputValue(value, variable) {
    if (!Number.isFinite(+value)) return "";
    return PCT_VARS.has(variable) ? String((+value * 100).toFixed(2)) : String((+value).toFixed(2));
  }

  function parseEditableValue(raw, variable) {
    let text = String(raw || "").trim();
    if (!text) return NaN;
    const negative = /^\(.*\)$/.test(text);
    text = text.replace(/[,$%()\s]/g, "");
    let number = Number(text);
    if (!Number.isFinite(number)) return NaN;
    if (negative) number = -Math.abs(number);
    return PCT_VARS.has(variable) ? number / 100 : number;
  }

  function updateSubtitle(mapping) {
    const subtitle = mapping.querySelector(".mapping-head p");
    if (!subtitle) return;
    const sectionNote = subtitle.textContent.match(/( · .*?)\.$/)?.[1] || "";
    subtitle.textContent = `Suggested mappings are shaded. ✓ means the printed column also matches the schedule math. Review any column or continue when the calculation is complete${sectionNote}.`;
  }

  function updateColumnStates(mapping) {
    mapping.querySelectorAll(".mapping-select").forEach((select) => {
      const th = select.closest("th");
      const note = select.nextElementSibling?.classList.contains("mapping-validation-note")
        ? select.nextElementSibling : null;
      const state = select.classList.contains("inferred") ? "inferred"
        : select.classList.contains("user") ? "user"
          : select.classList.contains("suggested") ? "suggested" : "unmapped";
      const mathNote = note?.textContent.trim() || "";

      if (th) {
        ["mapping-suggested", "mapping-user", "mapping-inferred"].forEach((name) =>
          th.classList.toggle(name, name === `mapping-${state}`));
        const head = th.querySelector(".mapping-doc-head");
        let check = head?.querySelector(".mapping-math-check");
        if (mathNote && select.value && head) {
          if (!check) {
            check = document.createElement("span");
            check.className = "mapping-math-check";
            check.textContent = "✓";
            head.appendChild(check);
          }
          check.title = mathNote;
          check.setAttribute("aria-label", mathNote);
        } else check?.remove();
      }
    });
  }

  function updateInferenceSummary(mapping) {
    const rail = mapping.querySelector("#mappingRail");
    if (!rail) return;
    const confirmed = [...mapping.querySelectorAll(".mapping-select")].map((select) => ({
      header: headerLabel(select),
      variable: selectedLabel(select),
      reason: select.nextElementSibling?.textContent.trim() || "",
    })).filter((item) => item.variable && item.reason);
    let summary = rail.querySelector(`.${SUMMARY_CLASS}`);
    if (!confirmed.length) {
      summary?.remove();
      return;
    }

    const signature = JSON.stringify(confirmed);
    if (!summary) {
      summary = document.createElement("div");
      summary.className = SUMMARY_CLASS;
      const conflict = rail.querySelector(".mapping-conflict");
      rail.insertBefore(summary, conflict || rail.querySelector("#mappingAnalyze"));
    }
    if (summary.dataset.signature === signature) return;
    summary.dataset.signature = signature;
    summary.innerHTML = `<strong>Math-confirmed columns</strong><div class="mapping-inferred-list">${confirmed.map((item) =>
      `<div class="mapping-inferred-item"><b>✓ ${escapeHtml(item.header)} → ${escapeHtml(item.variable)}</b><span>${escapeHtml(item.reason)}</span></div>`
    ).join("")}</div>`;
  }

  function tableRows(rep) {
    if (Array.isArray(rep?.table?.values)) return rep.table.values;
    const columns = rep?.table?.columns || [];
    return (rep?.table?.rows || []).map((row) =>
      columns.map((column) => row.values?.[column.variable] ?? null));
  }

  function tableRowLabels(rep) {
    if (typeof window.tableLabels === "function") return window.tableLabels(rep?.table);
    const table = rep?.table || {};
    const n = tableRows(rep).length;
    return Array.from({ length: n }, (_, index) =>
      table.job_names?.[index] || table.job_ids?.[index] || table.job_labels?.[index] || `Row ${index + 1}`);
  }

  function displayMapping(state) {
    const mapping = { ...state.mapping };
    Object.entries(state.inferred || {}).forEach(([column, match]) => {
      mapping[column] = match.variable;
    });
    return mapping;
  }

  function columnByVariable(mapping) {
    const result = {};
    Object.entries(mapping || {}).forEach(([column, variable]) => {
      if (variable && result[variable] == null) result[variable] = +column;
    });
    return result;
  }

  function ensureOriginalRows(rep) {
    if (!Array.isArray(rep._manualOriginalRows))
      rep._manualOriginalRows = tableRows(rep).map((row) => [...row]);
    return rep._manualOriginalRows;
  }

  function effectiveRows(rep, mapping) {
    const rows = ensureOriginalRows(rep).map((row) => [...row]);
    const columns = columnByVariable(mapping);
    Object.entries(rep._manualEdits || {}).forEach(([rowText, edits]) => {
      const rowIndex = +rowText;
      if (!rows[rowIndex]) return;
      Object.entries(edits || {}).forEach(([variable, value]) => {
        const column = columns[variable];
        if (column != null && Number.isFinite(+value)) rows[rowIndex][column] = +value;
      });
    });
    return rows;
  }

  function applyEffectiveRowsToTable(rep, rows) {
    if (Array.isArray(rep?.table?.values)) rep.table.values = rows.map((row) => [...row]);
    else if (Array.isArray(rep?.table?.rows)) {
      const columns = rep.table.columns || [];
      rep.table.rows.forEach((row, rowIndex) => {
        if (!row || !rows[rowIndex]) return;
        row.values = row.values || {};
        columns.forEach((column, columnIndex) => {
          if (column?.variable) row.values[column.variable] = rows[rowIndex][columnIndex];
        });
      });
    }
  }

  function rebuildManualJobs(rep, mapping) {
    rep._manualMapping = { ...mapping };
    rep._manualEdits = rep._manualEdits || {};
    const rows = effectiveRows(rep, mapping);
    const labels = tableRowLabels(rep);
    const jobs = rows.map((row, rowIndex) => {
      const printed = {};
      Object.entries(mapping).forEach(([column, variable]) => {
        const raw = row[+column];
        const value = raw === null || raw === "" ? NaN : +raw;
        if (variable && Number.isFinite(value)) printed[variable] = value;
      });
      return { label: labels[rowIndex] || `Row ${rowIndex + 1}`, ...window.WippleMath.deriveCanonicalVars(printed) };
    });
    applyEffectiveRowsToTable(rep, rows);
    rep._manualEffectiveRows = rows;
    rep.analysis = {
      ...(rep.analysis || {}),
      schema: "wip",
      basis: "user-mapped",
      jobs,
      corrections: [],
      signals: [],
      kpis: null,
      coverage: {
        ...(rep.analysis?.coverage || {}),
        mapped_cols: Object.keys(mapping).length,
        numeric_cols: (rep.table?.columns || []).length,
      },
    };
    rep._manualMappingAudit = window.WippleMath.auditFixedMapping(rows, mapping, labels);
  }

  function annotateTotals(rep, mapping) {
    const payload = [rep?.totals, rep?.validation?.totals, rep?.validator?.totals,
      rep?.analysis?.totals, rep?._validation?.totals].find(Array.isArray);
    if (!payload) return;
    const numericMap = (rep?.parse?.numeric_col_map || []).map(Number);
    payload.forEach((item) => {
      if (item.variable) return;
      let matrixColumn = +item.column;
      if (mapping[matrixColumn] == null) {
        const mappedIndex = numericMap.indexOf(+item.column);
        if (mappedIndex >= 0) matrixColumn = mappedIndex;
      }
      if (mapping[matrixColumn]) item.variable = mapping[matrixColumn];
    });
  }

  function rowEdits(rep, rowIndex) {
    return rep?._manualEdits?.[rowIndex] || {};
  }

  function hasRowEdits(rep, rowIndex) {
    return Object.keys(rowEdits(rep, rowIndex)).length > 0;
  }

  function sortedVariables(variables) {
    const set = new Set(variables || []);
    return VAR_ORDER.filter((variable) => set.has(variable));
  }

  function manualAuditRows(rep) {
    const auditRows = new Map((rep?._manualMappingAudit?.failedRows || [])
      .map((failure) => [failure.rowIndex, failure]));
    Object.keys(rep?._manualEdits || {}).forEach((rowText) => {
      const rowIndex = +rowText;
      if (!auditRows.has(rowIndex)) auditRows.set(rowIndex, {
        rowIndex,
        rowLabel: tableRowLabels(rep)[rowIndex] || `Row ${rowIndex + 1}`,
        variables: Object.keys(rowEdits(rep, rowIndex)),
        details: [],
        relations: [],
      });
    });
    if (Number.isInteger(rep?._manualExtraEditRow) && !auditRows.has(rep._manualExtraEditRow)) {
      auditRows.set(rep._manualExtraEditRow, {
        rowIndex: rep._manualExtraEditRow,
        rowLabel: tableRowLabels(rep)[rep._manualExtraEditRow] || `Row ${rep._manualExtraEditRow + 1}`,
        variables: Object.values(rep._manualMapping || {}),
        details: [],
        relations: [],
      });
    }
    return [...auditRows.values()].sort((a, b) => a.rowIndex - b.rowIndex);
  }

  function manualAuditCardHTML(rep, failure) {
    const mapping = rep._manualMapping || {};
    const columns = columnByVariable(mapping);
    const originals = ensureOriginalRows(rep);
    const current = rep._manualEffectiveRows || effectiveRows(rep, mapping);
    const edits = rowEdits(rep, failure.rowIndex);
    const variables = sortedVariables((failure.variables || []).length
      ? failure.variables : Object.keys(mapping));
    const edited = hasRowEdits(rep, failure.rowIndex);
    const failed = (failure.details || []).length > 0;
    const facts = variables.map((variable) => {
      const column = columns[variable];
      if (column == null) return "";
      const currentValue = +current[failure.rowIndex]?.[column];
      const printedValue = +originals[failure.rowIndex]?.[column];
      const changed = Number.isFinite(currentValue) && Number.isFinite(printedValue)
        && Math.abs(currentValue - printedValue) > (PCT_VARS.has(variable) ? 1e-9 : .004);
      return `<div class="manual-audit-fact"><span>${escapeHtml(variableLabel(variable))}</span><b>${escapeHtml(formatValue(currentValue, variable))}</b>${changed
        ? `<small>Printed ${escapeHtml(formatValue(printedValue, variable))}</small>` : ""}</div>`;
    }).join("");
    const clues = (failure.details || []).map((detail) => {
      const inputs = detail.variables.filter((variable) => variable !== detail.outputVariable)
        .map(variableLabel).join(", ");
      return `<div class="manual-audit-equation"><strong>${escapeHtml(variableLabel(detail.outputVariable))} does not agree</strong><span>Using ${escapeHtml(inputs)}, the row implies ${escapeHtml(formatValue(detail.expected, detail.outputVariable))}; the current value is ${escapeHtml(formatValue(detail.observed, detail.outputVariable))}.</span><small>Difference ${escapeHtml(formatValue(Math.abs(detail.difference), detail.outputVariable))}. This is a clue, not proof that the output field is the bad cell.</small></div>`;
    }).join("");
    const fields = variables.map((variable) => {
      const column = columns[variable];
      if (column == null) return "";
      const currentValue = +current[failure.rowIndex]?.[column];
      const printedValue = +originals[failure.rowIndex]?.[column];
      return `<div class="manual-edit-field"><label>${escapeHtml(variableLabel(variable))}</label><input type="text" inputmode="decimal" data-variable="${variable}" value="${escapeHtml(inputValue(currentValue, variable))}" aria-label="Edit ${escapeHtml(variableLabel(variable))} for ${escapeHtml(failure.rowLabel)}"><small>Printed ${escapeHtml(formatValue(printedValue, variable))}</small></div>`;
    }).join("");
    return `<article class="manual-audit-row${edited ? " edited" : ""}" data-manual-row="${failure.rowIndex}">
      <div class="manual-audit-row-head"><div><strong>${escapeHtml(failure.rowLabel)}${edited ? '<span class="manual-edit-badge">reviewer edit</span>' : ""}</strong><small>${failed
        ? `${failure.details.length} available consistency check${failure.details.length === 1 ? "" : "s"} failed`
        : "Reviewer-entered values; available checks currently pass"}</small></div><button class="btn manual-edit-toggle" type="button">Edit values</button></div>
      <div class="manual-audit-facts">${facts}</div>${clues ? `<div class="manual-audit-equations">${clues}</div>` : ""}
      <form class="manual-edit-form hidden" data-edit-row="${failure.rowIndex}"><div class="manual-edit-grid">${fields}</div><div class="manual-edit-actions"><span class="manual-edit-status"></span>${edited
        ? '<button class="btn manual-reset-row" type="button">Reset to printed</button>' : ""}<button class="btn primary" type="submit">Save and recalculate</button></div></form>
    </article>`;
  }

  function manualAuditSectionHTML(rep) {
    if (rep?.overall_status !== "user_mapped_unverified" || !rep?._manualMapping) return "";
    const failures = rep?._manualMappingAudit?.failedRows || [];
    const cards = manualAuditRows(rep).map((failure) => manualAuditCardHTML(rep, failure)).join("");
    const labels = tableRowLabels(rep);
    const currentExtra = Number.isInteger(rep._manualExtraEditRow) ? rep._manualExtraEditRow : -1;
    return `<section class="manual-audit-details"><header><div><strong>${failures.length
      ? `${failures.length} job${failures.length === 1 ? "" : "s"} need value review`
      : "Mapped values reviewed"}</strong><p>${failures.length
        ? "The values below disagree with an available equation. Wipple cannot prove which input is wrong, so nothing is auto-corrected: edit any mapped field, save, and the audit and analysis recalculate immediately."
        : "Available mapped-column checks currently pass. Reviewer edits remain visible and reversible below."}</p></div></header>
      ${cards ? `<div class="manual-audit-list">${cards}</div>` : ""}
      <details class="manual-edit-any"><summary>Edit another mapped job</summary><div class="manual-edit-any-controls"><select id="manualOtherJob" aria-label="Choose another job to edit"><option value="">Choose a job</option>${labels.map((label, index) => `<option value="${index}" ${index === currentExtra ? "selected" : ""}>${escapeHtml(label || `Row ${index + 1}`)}</option>`).join("")}</select><button class="btn" id="manualOtherOpen" type="button">Edit values</button></div></details>
    </section>`;
  }

  function focusManualRow(rowIndex, openEditor) {
    requestAnimationFrame(() => {
      const card = document.querySelector(`#certificate [data-manual-row="${rowIndex}"]`);
      if (!card) return;
      card.classList.add("focused");
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      if (openEditor) card.querySelector(".manual-edit-toggle")?.click();
    });
  }

  function saveManualRow(rep, form) {
    const rowIndex = +form.dataset.editRow;
    const columns = columnByVariable(rep._manualMapping || {});
    const originals = ensureOriginalRows(rep);
    const next = { ...rowEdits(rep, rowIndex) };
    const status = form.querySelector(".manual-edit-status");
    let invalid = "";
    form.querySelectorAll("input[data-variable]").forEach((input) => {
      const variable = input.dataset.variable;
      const value = parseEditableValue(input.value, variable);
      if (!Number.isFinite(value)) {
        invalid = `${variableLabel(variable)} needs a number.`;
        return;
      }
      const column = columns[variable];
      const printed = +originals[rowIndex]?.[column];
      const tolerance = PCT_VARS.has(variable) ? 1e-9 : .004;
      if (Number.isFinite(printed) && Math.abs(value - printed) <= tolerance) delete next[variable];
      else next[variable] = value;
    });
    if (invalid) {
      if (status) status.textContent = invalid;
      return;
    }
    rep._manualEdits = rep._manualEdits || {};
    if (Object.keys(next).length) rep._manualEdits[rowIndex] = next;
    else delete rep._manualEdits[rowIndex];
    rep._manualExtraEditRow = null;
    rebuildManualJobs(rep, rep._manualMapping);
    window.renderCertificate(rep);
    focusManualRow(rowIndex, false);
  }

  function resetManualRow(rep, rowIndex) {
    if (rep._manualEdits) delete rep._manualEdits[rowIndex];
    rep._manualExtraEditRow = null;
    rebuildManualJobs(rep, rep._manualMapping);
    window.renderCertificate(rep);
    focusManualRow(rowIndex, false);
  }

  function wireManualAuditSection(rep) {
    const section = document.querySelector("#certificate .manual-audit-details");
    if (!section) return;
    section.querySelectorAll(".manual-edit-toggle").forEach((button) => {
      button.onclick = () => {
        const card = button.closest(".manual-audit-row");
        const form = card?.querySelector(".manual-edit-form");
        if (!form) return;
        const opening = form.classList.contains("hidden");
        form.classList.toggle("hidden", !opening);
        button.textContent = opening ? "Close editor" : "Edit values";
        if (opening) form.querySelector("input")?.focus();
      };
    });
    section.querySelectorAll(".manual-edit-form").forEach((form) => {
      form.onsubmit = (event) => {
        event.preventDefault();
        saveManualRow(rep, form);
      };
      form.querySelector(".manual-reset-row")?.addEventListener("click", () =>
        resetManualRow(rep, +form.dataset.editRow));
    });
    const choose = section.querySelector("#manualOtherJob");
    const open = section.querySelector("#manualOtherOpen");
    if (choose && open) open.onclick = () => {
      const rowIndex = +choose.value;
      if (!Number.isInteger(rowIndex) || rowIndex < 0) return;
      rep._manualExtraEditRow = rowIndex;
      window.renderCertificate(rep);
      focusManualRow(rowIndex, true);
    };
  }

  function openValidation() {
    const nav = document.getElementById("navCert");
    if (nav) nav.click();
  }

  function installBehaviorPatches() {
    if (window.__wippleManualAuditPatched || !window.WippleMath) return;
    const originalApply = window.applyColumnMapping;
    const originalTotalsDetail = window.totalsDetail;
    const originalChecks = window.computeValidationChecks;
    const originalCertificate = window.renderCertificate;
    const originalDash = window.renderDash;
    if (![originalApply, originalTotalsDetail, originalChecks, originalCertificate, originalDash]
      .every((fn) => typeof fn === "function")) return;
    window.__wippleManualAuditPatched = true;

    window.applyColumnMapping = function patchedApplyColumnMapping(rep, state) {
      originalApply(rep, state);
      const mapping = displayMapping(state);
      ensureOriginalRows(rep);
      rebuildManualJobs(rep, mapping);
      annotateTotals(rep, mapping);
      rep.findings = [];
      rep.witnesses = [];
      rep.validator_status = "manual_mapping_audit";
      rep.overall_status = "user_mapped_unverified";
      rep.fallback_notes = "Column mapping was reviewed. Available fixed-mapping consistency checks were run without certifying the mapping.";
    };

    window.totalsDetail = function patchedTotalsDetail(rep, accepted) {
      const detail = originalTotalsDetail(rep, accepted);
      if (!detail || rep?.overall_status !== "user_mapped_unverified") return detail;
      const mismatches = (detail.mismatches || []).map((item) => ({
        ...item,
        status: item.status === "unassessed" ? "unassessed" : "mapped_sum_mismatch",
        explained: false,
        proposedCorrection: null,
      }));
      return {
        ...detail,
        mismatches,
        allExplained: false,
        totalCorrections: [],
      };
    };

    window.computeValidationChecks = function patchedComputeValidationChecks(rep, accepted) {
      const result = originalChecks(rep, accepted);
      if (rep?.overall_status !== "user_mapped_unverified") return result;
      const audit = rep._manualMappingAudit;
      const mappingCheck = result.checks.find((check) => check.label === "Column mapping reviewed");
      if (mappingCheck) mappingCheck.note = "The assignments were used for calculation. Available row identities and stated totals were checked where possible, but the mapping and untested columns are not certified.";

      if (audit?.relations?.length) {
        const failed = audit.failedRows || [];
        const labels = failed.slice(0, 5).map((row) => row.rowLabel).join("; ");
        result.checks.splice(Math.min(2, result.checks.length), 0, failed.length ? {
          st: "bad",
          label: `${failed.length} job${failed.length === 1 ? "" : "s"} fail the available mapped-column consistency check`,
          note: `${labels}${failed.length > 5 ? `; and ${failed.length - 5} more` : ""}. Open the value review below to see the current numbers and edit any mapped field.`,
        } : {
          st: "ok",
          label: `Available mapped-column identities agree across ${audit.checkedRows} jobs`,
          note: "This is a limited consistency audit, not mathematical certification of the mapping.",
        });
      }

      result.checks.forEach((check) => {
        check.label = check.label
          .replace("Stated totals match the validated column sums", "Stated totals match the mapped job sums")
          .replace("Stated totals disagree with the validated column sums", "Stated totals disagree with the mapped job sums")
          .replace("One or more validated totals could not be assessed", "One or more stated totals could not be assessed against the mapped jobs")
          .replace("A stated total conflicts with the independently proven job corrections", "A stated total conflicts with the mapped job values");
        check.note = String(check.note || "")
          .replace(/validated rows/g, "mapped jobs")
          .replace(/validated column sums/g, "mapped job sums")
          .replace(/independently validated job values/g, "mapped job values");
      });
      result.nBad = result.checks.filter((check) => check.st === "bad").length;
      result.nFixed = result.checks.filter((check) => check.st === "fixed").length;
      result.nWarn = result.checks.filter((check) => check.st === "warn").length;
      result.passed = result.checks.filter((check) => check.st === "ok" || check.st === "fixed").length;
      result.head = result.nBad ? `${result.passed} of ${result.checks.length} checks passed` : "Column mapping reviewed";
      return result;
    };

    window.renderCertificate = function patchedRenderCertificate(rep) {
      originalCertificate(rep);
      const fold = document.querySelector("#certificate .checks-fold");
      if (!fold || document.querySelector("#certificate .manual-audit-details")) return;
      const html = manualAuditSectionHTML(rep);
      if (!html) return;
      fold.insertAdjacentHTML("afterend", html);
      wireManualAuditSection(rep);
    };

    window.renderDash = function patchedRenderDash(rep) {
      originalDash(rep);
      if (rep?.overall_status !== "user_mapped_unverified") return;
      const failures = rep?._manualMappingAudit?.failedRows || [];
      if (!failures.length) return;
      const banner = document.querySelector("#dash .banner");
      if (!banner) return;
      banner.classList.add("manual-audit-warning");
      banner.setAttribute("role", "button");
      banner.setAttribute("tabindex", "0");
      banner.setAttribute("aria-label", "Open Validation to review mapped values that failed consistency checks");
      banner.innerHTML = `<strong>${failures.length} job${failures.length === 1 ? "" : "s"} fail an available consistency check.</strong><span>Column assignments were reviewed but not mathematically certified. Open Validation to review or edit the values before relying on the figures.</span>`;
      banner.onclick = openValidation;
      banner.onkeydown = (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openValidation();
        }
      };
    };
  }

  function refresh() {
    refreshQueued = false;
    const mapping = document.getElementById("mapping");
    if (!mapping || mapping.classList.contains("hidden")) return;
    updateSubtitle(mapping);
    updateColumnStates(mapping);
    updateInferenceSummary(mapping);
  }

  function queueRefresh() {
    if (refreshQueued) return;
    refreshQueued = true;
    requestAnimationFrame(refresh);
  }

  function start() {
    installStyles();
    installBehaviorPatches();
    const mapping = document.getElementById("mapping");
    if (!mapping) return;
    new MutationObserver(queueRefresh).observe(mapping, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["class"],
    });
    mapping.addEventListener("change", queueRefresh);
    queueRefresh();
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
