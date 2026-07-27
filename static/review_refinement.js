(function () {
  "use strict";

  const STYLE_ID = "wipple-review-refinement";
  let observerQueued = false;
  let currentReport = null;

  function installStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      .mapping-inferred-summary{max-height:210px;overflow:hidden;display:flex;flex-direction:column}
      .mapping-inferred-summary .mapping-inferred-list{overflow:auto;padding-right:3px;overscroll-behavior:contain}
      .mapping-inferred-summary .mapping-inferred-list::-webkit-scrollbar{width:6px}
      .mapping-inferred-summary .mapping-inferred-list::-webkit-scrollbar-thumb{background:#B8C5B4;border-radius:999px}
      #mappingRail #mappingAnalyze{position:relative;z-index:1}

      #certificate .manual-audit-details{
        width:min(920px,calc(100% - 32px));max-width:none;margin:18px auto 0;
        padding:0;border:0;background:transparent;box-sizing:border-box
      }
      #certificate .manual-audit-details>header{display:block;width:100%;margin:0 0 9px;padding:0}
      #certificate .manual-audit-details>header strong{font-size:19px;line-height:1.2;color:var(--amber)}
      #certificate .manual-audit-details>header p{display:none}
      #certificate .manual-audit-list{gap:7px}
      #certificate .manual-audit-row{padding:9px 10px;border-radius:8px}
      #certificate .manual-audit-row-head{align-items:center}
      #certificate .manual-audit-row-head strong{font-size:12.5px}
      #certificate .manual-audit-row-head small{font-size:10px;margin-top:1px;color:var(--brick)}
      #certificate .manual-audit-row-head .btn{padding:3px 8px}
      #certificate .manual-audit-facts{grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:5px;margin-top:7px}
      #certificate .manual-audit-fact{padding:5px 7px;border-radius:6px}
      #certificate .manual-audit-fact span{font-size:9px}
      #certificate .manual-audit-fact b{font-size:11px;margin-top:1px}
      #certificate .manual-audit-fact.likely-shared-input{
        border-color:#C66E5B;background:#F7E9E5;box-shadow:inset 0 0 0 1px rgba(163,64,47,.08)
      }
      #certificate .manual-audit-fact.likely-shared-input span,
      #certificate .manual-audit-fact.likely-shared-input b{color:var(--brick)}
      #certificate .manual-audit-fact.likely-shared-input::after{
        content:"Shared by the failed checks";display:block;margin-top:2px;
        font-size:8.5px;font-weight:650;color:var(--brick)
      }
      #certificate .manual-audit-equations{display:none}
      #certificate .manual-audit-why{margin-top:6px;border-top:1px solid var(--line-soft);padding-top:5px}
      #certificate .manual-audit-why summary{
        cursor:pointer;display:inline-block;list-style:none;font-size:9.5px;font-weight:600;
        color:var(--muted);border-bottom:1px dotted var(--muted)
      }
      #certificate .manual-audit-why summary::-webkit-details-marker{display:none}
      #certificate .manual-audit-why[open] summary{margin-bottom:5px}
      #certificate .manual-audit-why .manual-audit-equations{display:flex;margin-top:0}
      #certificate .manual-audit-equation{padding:4px 7px}
      #certificate .manual-audit-equation strong{font-size:10px}
      #certificate .manual-audit-equation span{font-size:9.5px}
      #certificate .manual-audit-equation small{font-size:8.5px}
      #certificate .manual-edit-any{margin-top:8px}

      @media(max-width:620px){
        #certificate .manual-audit-details{width:min(100%,calc(100% - 20px))}
        #certificate .manual-audit-details>header strong{font-size:17px}
      }
    `;
    document.head.appendChild(style);
  }

  function variableLabel(variable) {
    const names = {
      V: "Contract Value", C: "Estimated Total Cost", G: "Estimated Gross Profit",
      D: "Cost to Date", Q: "Cost to Complete", E: "Earned Revenue",
      B: "Billings to Date", H: "Earned Gross Profit to Date",
      N: "Net Billing Position", U: "Underbillings", O: "Overbillings",
      R: "Remaining Revenue", RB: "Remaining Billings", M: "Gross Margin %",
      P: "Percent Complete", PB: "Percent Billed",
    };
    return names[variable] || variable;
  }

  function rememberConfirmedVariables() {
    if (window.__wippleReviewConfirmedPatched || typeof window.applyColumnMapping !== "function") return;
    const previous = window.applyColumnMapping;
    window.applyColumnMapping = function refinedApplyColumnMapping(rep, state) {
      const result = previous.apply(this, arguments);
      currentReport = rep;
      rep._manualConfirmedVariables = [...new Set(Object.values(state?.inferred || {})
        .filter((match) => match?.confirmed)
        .map((match) => match.variable)
        .filter(Boolean))];
      return result;
    };
    window.__wippleReviewConfirmedPatched = true;
  }

  function rememberRenderedReport() {
    if (window.__wippleReviewRenderPatched || typeof window.renderCertificate !== "function") return;
    const previous = window.renderCertificate;
    window.renderCertificate = function refinedRenderCertificate(rep) {
      currentReport = rep;
      const result = previous.apply(this, arguments);
      queueRefine();
      return result;
    };
    window.__wippleReviewRenderPatched = true;
  }

  function failedNames(failure) {
    const outputs = [...new Set((failure?.details || []).map((detail) => detail.outputVariable).filter(Boolean))];
    return outputs.map(variableLabel);
  }

  function sharedUnconfirmedVariable(rep, failure) {
    const details = failure?.details || [];
    if (details.length < 2) return null;
    const counts = new Map();
    details.forEach((detail) => {
      new Set(detail.variables || []).forEach((variable) =>
        counts.set(variable, (counts.get(variable) || 0) + 1));
    });
    const confirmed = new Set(rep?._manualConfirmedVariables || []);
    const shared = [...counts.entries()]
      .filter(([variable, count]) => count === details.length && !confirmed.has(variable))
      .map(([variable]) => variable);
    return shared.length === 1 ? shared[0] : null;
  }

  function compactCard(rep, card, failure) {
    if (card.dataset.reviewRefined === "true") return;
    card.dataset.reviewRefined = "true";

    const subtitle = card.querySelector(".manual-audit-row-head small");
    const names = failedNames(failure);
    if (subtitle && names.length) {
      const count = failure.details.length;
      subtitle.textContent = `${count} available consistency check${count === 1 ? "" : "s"} failed: ${names.join(", ")}`;
    }

    const equations = card.querySelector(".manual-audit-equations");
    if (equations) {
      const details = document.createElement("details");
      details.className = "manual-audit-why";
      const summary = document.createElement("summary");
      summary.textContent = "Why flagged";
      equations.parentNode.insertBefore(details, equations);
      details.append(summary, equations);
    }

    const shared = sharedUnconfirmedVariable(rep, failure);
    if (shared) {
      const variables = failure.variables || [];
      const index = variables.indexOf(shared);
      const facts = card.querySelectorAll(".manual-audit-fact");
      if (index >= 0 && facts[index]) {
        facts[index].classList.add("likely-shared-input");
        facts[index].title = `${variableLabel(shared)} is the only unconfirmed value shared by every failed check. Review it first; this is not proof that it is wrong.`;
      }
    }
  }

  function refineReview() {
    observerQueued = false;
    rememberConfirmedVariables();
    rememberRenderedReport();
    const rep = currentReport;
    const section = document.querySelector("#certificate .manual-audit-details");
    if (!section || !rep?._manualMappingAudit) return;

    const header = section.querySelector(":scope > header strong");
    const failures = rep._manualMappingAudit.failedRows || [];
    if (header) header.textContent = failures.length
      ? `${failures.length} job${failures.length === 1 ? "" : "s"} need review`
      : "Mapped values reviewed";

    const byRow = new Map(failures.map((failure) => [failure.rowIndex, failure]));
    section.querySelectorAll(".manual-audit-row[data-manual-row]").forEach((card) => {
      const failure = byRow.get(+card.dataset.manualRow);
      if (failure) compactCard(rep, card, failure);
    });
  }

  function queueRefine() {
    if (observerQueued) return;
    observerQueued = true;
    requestAnimationFrame(refineReview);
  }

  function start() {
    installStyles();
    rememberConfirmedVariables();
    rememberRenderedReport();
    new MutationObserver(queueRefine).observe(document.body, { childList: true, subtree: true });
    queueRefine();
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
