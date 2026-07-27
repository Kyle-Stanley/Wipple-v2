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
      #mapping{padding:6px 0 48px}
      .mapping-wide{width:calc(100vw - 24px);max-width:none}
      .mapping-head{margin-bottom:10px}
      .mapping-head h2{font-size:27px}
      .mapping-head p{margin-top:3px}
      .mapping-layout{grid-template-columns:minmax(0,1fr) 270px;gap:10px}
      .mapping-table-note{padding:8px 12px}
      .mapping-scroll{max-height:calc(100vh - 220px)}
      .mapping-grid th,.mapping-grid td{
        width:140px;min-width:132px;max-width:148px;padding:6px 8px;
        overflow:hidden;text-overflow:ellipsis;white-space:nowrap
      }
      .mapping-grid th{padding:8px;white-space:normal}
      .mapping-grid th:first-child,.mapping-grid td:first-child{
        width:108px;min-width:108px;max-width:108px
      }
      .mapping-grid th:nth-child(2),.mapping-grid td:nth-child(2){
        width:230px;min-width:230px;max-width:230px;text-align:left
      }
      .mapping-grid td:nth-child(2){overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .mapping-doc-head{min-height:27px;margin-bottom:5px}
      .mapping-select{min-height:30px;border-width:1.5px;font-weight:600}
      .mapping-select.suggested{background:#DDE8D9;border-color:#92A88E;color:#2F4935}
      .mapping-select.user{background:#D7E4D4;border-color:var(--sage-deep);color:var(--sage-deep);box-shadow:0 0 0 1px rgba(58,76,62,.12)}
      .mapping-select.inferred{background:#DDECF2;border-color:#7EA7B7;color:#294F5E}
      .mapping-validation-note{min-height:12px;margin-top:3px;font-size:9.5px;font-weight:600}
      .mapping-grid th.mapping-suggested{background:#F0F5ED;box-shadow:inset 0 3px 0 #92A88E}
      .mapping-grid th.mapping-user{background:#EDF3EA;box-shadow:inset 0 3px 0 var(--sage-deep)}
      .mapping-grid th.mapping-inferred{background:#EEF6F8;box-shadow:inset 0 3px 0 #6D9BAE}
      .mapping-rail{top:8px;padding:15px}
      .mapping-inferred-summary{margin:0 0 12px;padding:10px;border:1px solid #B9CFD8;border-radius:9px;background:#EEF6F8;color:#294F5E}
      .mapping-inferred-summary>strong{display:block;font-size:10.5px;letter-spacing:.055em;text-transform:uppercase;margin-bottom:6px}
      .mapping-inferred-list{display:flex;flex-direction:column;gap:5px}
      .mapping-inferred-item{font-size:10.5px;line-height:1.35}
      .mapping-inferred-item b{display:block;font-size:11px;color:#244653}
      .mapping-inferred-item span{color:#527381}
      .mapping-derived{margin-top:7px}
      @media(max-width:900px){
        .mapping-wide{width:min(100%,calc(100vw - 28px))}
        .mapping-layout{grid-template-columns:1fr}
        .mapping-rail{position:static;grid-row:1}
        .mapping-scroll{max-height:60vh}
      }
      @media(max-width:620px){
        #mapping{padding-top:4px}
        .mapping-grid th:nth-child(2),.mapping-grid td:nth-child(2){width:190px;min-width:190px;max-width:190px}
      }
    `;
    document.head.appendChild(style);
  }

  function selectedLabel(select) {
    const option = select.options[select.selectedIndex];
    return option ? option.textContent.trim().replace(/\s+·\s+reference only$/, "") : "";
  }

  function headerLabel(select) {
    return select.closest("th")?.querySelector(".mapping-doc-head")?.textContent.trim() || "Column";
  }

  function setText(node, text) {
    if (node && node.textContent !== text) node.textContent = text;
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
      }
      if (state === "suggested" && note && !note.textContent.trim())
        setText(note, "Mapped from header");
      else if (state === "user" && note)
        setText(note, "Confirmed mapping");
      else if (state === "unmapped" && note)
        setText(note, "");
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
    summary.innerHTML = `<strong>Matched from the math</strong><div class="mapping-inferred-list">${inferred.map((item) =>
      `<div class="mapping-inferred-item"><b>${escapeHtml(item.header)} → ${escapeHtml(item.variable)}</b><span>${escapeHtml(item.reason)}</span></div>`
    ).join("")}</div>`;
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"]/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
    })[char]);
  }

  function refresh() {
    refreshQueued = false;
    const mapping = document.getElementById("mapping");
    if (!mapping || mapping.classList.contains("hidden")) return;
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
