(function () {
  "use strict";

  const STYLE_ID = "wipple-mapping-ui-enhancements";
  const SUMMARY_CLASS = "mapping-inferred-summary";
  let refreshQueued = false;

  function installStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      #mapping{padding:12px 0 56px}
      .mapping-wide{width:min(1480px,calc(100vw - 72px));max-width:none}
      .mapping-head{margin-bottom:12px}
      .mapping-head h2{font-size:27px}
      .mapping-head p{margin-top:3px}
      .mapping-layout{grid-template-columns:minmax(0,1fr) 260px;gap:14px}
      .mapping-table-note{padding:8px 12px}
      .mapping-scroll{max-height:calc(100vh - 224px)}
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
      .manual-audit-details{max-width:66ch;margin:16px auto 0;padding:12px 15px;border:1px solid #D9C28F;border-radius:9px;background:#F6F0E2;text-align:left}
      .manual-audit-details>strong{display:block;font-size:13px;color:var(--amber);margin-bottom:5px}
      .manual-audit-details>p{font-size:11.5px;color:var(--muted);margin-bottom:7px}
      .manual-audit-details ul{margin:0;padding-left:18px;font-size:12px}
      .manual-audit-details li{margin:4px 0}
      .manual-audit-details li span{display:block;color:var(--muted);font-size:10.5px}
      #dash .banner.manual-audit-warning strong{color:var(--brick)}
      @media(max-width:900px){
        .mapping-wide{width:min(100%,calc(100vw - 32px))}
        .mapping-layout{grid-template-columns:1fr}
        .mapping-rail{position:static;grid-row:1}
        .mapping-scroll{max-height:60vh}
      }
      @media(max-width:620px){
        #mapping{padding-top:6px}
        .mapping-grid th:nth-child(2),.mapping-grid td:nth-child(2){width:205px;min-width:205px;max-width:205px}
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

      if (th) {
        ["mapping-suggested", "mapping-user", "mapping-inferred"].forEach((name) =>
          th.classList.toggle(name, name === `mapping-${state}`));
        const head = th.querySelector(".mapping-doc-head");
        let check = head?.querySelector(".mapping-math-check");
        if (state === "inferred" && head) {
          if (!check) {
            check = document.createElement("span");
            check.className = "mapping-math-check";
            check.textContent = "✓";
            head.appendChild(check);
          }
          check.title = note?.textContent.trim() || "Matches the schedule math";
          check.setAttribute("aria-label", check.title);
        } else check?.remove();
      }
    });
  }

  function updateInferenceSummary(mapping) {
    const rail = mapping.querySelector("#mappingRail");
    if (!rail) return;
    const inferred = [...mapping.querySelectorAll(".mapping-select.inferred")].map((select) => ({
      header: headerLabel(select),
      variable: selectedLabel(select),
      reason: select.nextElementSibling?.textContent.trim() || "Matches the calculated values",
    }));
    let summary = rail.querySelector(`.${SUMMARY_CLASS}`);
    if (!inferred.length) {
      summary?.remove();
      return;
    }

    const signature = JSON.stringify(inferred);
    if (!summary) {
      summary = document.createElement("div");
      summary.className = SUMMARY_CLASS;
      const conflict = rail.querySelector(".mapping-conflict");
      rail.insertBefore(summary, conflict || rail.querySelector("#mappingAnalyze"));
    }
    if (summary.dataset.signature === signature) return;
    summary.dataset.signature = signature;
    summary.innerHTML = `<strong>Math-confirmed columns</strong><div class="mapping-inferred-list">${inferred.map((item) =>
      `<div class="mapping-inferred-item"><b>✓ ${escapeHtml(item.header)} → ${escapeHtml(item.variable)}</b><span>${escapeHtml(item.reason)}</span></div>`
    ).join("")}</div>`;
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"]/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
    })[char]);
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

  function rebuildManualJobs(rep, mapping) {
    const rows = tableRows(rep);
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
          note: `${audit.relations.map((relation) => relation.label).join("; ")}. ${labels}${failed.length > 5 ? `; and ${failed.length - 5} more` : ""}. This proves the row is internally inconsistent, not which printed cell is wrong.`,
        } : {
          st: "ok",
          label: `Available mapped-column identities agree across ${audit.checkedRows} jobs`,
          note: `${audit.relations.map((relation) => relation.label).join("; ")}. This is a limited consistency audit, not mathematical certification of the mapping.`,
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
      const failures = rep?._manualMappingAudit?.failedRows || [];
      if (!failures.length) return;
      const fold = document.querySelector("#certificate .checks-fold");
      if (!fold || document.querySelector("#certificate .manual-audit-details")) return;
      const section = document.createElement("section");
      section.className = "manual-audit-details";
      section.innerHTML = `<strong>Rows needing review</strong><p>The available equations disagree, but this sparse schedule does not contain enough independent evidence to name or replace the bad cell.</p><ul>${failures.map((failure) =>
        `<li><b>${escapeHtml(failure.rowLabel)}</b><span>${escapeHtml(failure.relations.join("; "))}</span></li>`
      ).join("")}</ul>`;
      fold.insertAdjacentElement("afterend", section);
    };

    window.renderDash = function patchedRenderDash(rep) {
      originalDash(rep);
      if (rep?.overall_status !== "user_mapped_unverified") return;
      const failures = rep?._manualMappingAudit?.failedRows || [];
      if (!failures.length) return;
      const banner = document.querySelector("#dash .banner");
      if (!banner) return;
      banner.classList.add("manual-audit-warning");
      banner.innerHTML = `Column assignments were reviewed before calculation but could not be mathematically certified. <strong>${failures.length} job${failures.length === 1 ? "" : "s"} fail an available consistency check; review Validation before relying on the figures.</strong>`;
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
