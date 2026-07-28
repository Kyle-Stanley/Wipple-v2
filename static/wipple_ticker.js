(() => {
  const PHRASES = ["Watch me wipple", "now watch me nay nayle"];
  const INTERVAL_MS = 2500;
  let phraseIndex = 0;
  let ticker = null;

  const processingVisible = () => {
    const section = document.querySelector("#processing");
    const running = typeof RUNNING === "undefined" || RUNNING;
    return Boolean(running && section && !section.classList.contains("hidden"));
  };

  const phrase = () => PHRASES[phraseIndex];

  const ensureSingleLine = () => {
    const log = document.querySelector("#log");
    if (!log || log.classList.contains("hidden")) return null;
    let line = log.querySelector(".wipple-stinger");
    if (!line) {
      line = document.createElement("div");
      line.className = "ln wipple-stinger";
      const bullet = document.createElement("span");
      bullet.className = "dot";
      bullet.textContent = "·";
      const text = document.createElement("span");
      line.appendChild(bullet);
      line.appendChild(text);
      log.replaceChildren(line);
    }
    return line.lastElementChild;
  };

  const showPhrase = () => {
    if (!processingVisible()) return;
    const base = phrase();
    const lanes = [...document.querySelectorAll("#batchLanes .lane.processing .lane-msg")];
    if (lanes.length) {
      lanes.forEach(el => {
        el.dataset.base = base;
        el.textContent = `${base}...`;
      });
      return;
    }
    const text = ensureSingleLine();
    if (text) {
      text.dataset.base = base;
      text.textContent = `${base}...`;
      if (typeof LATEST_PROGRESS !== "undefined") LATEST_PROGRESS = text;
    }
  };

  const stopTicker = () => {
    if (ticker !== null) clearInterval(ticker);
    ticker = null;
    phraseIndex = 0;
  };

  const startTicker = () => {
    if (!processingVisible()) return;
    if (ticker === null) {
      phraseIndex = 0;
      showPhrase();
      ticker = setInterval(() => {
        if (!processingVisible()) {
          stopTicker();
          return;
        }
        phraseIndex = (phraseIndex + 1) % PHRASES.length;
        showPhrase();
      }, INTERVAL_MS);
      return;
    }
    showPhrase();
  };

  addLine = () => startTicker();

  const originalUpdateBatchLane = updateBatchLane;
  updateBatchLane = i => {
    originalUpdateBatchLane(i);
    const item = BATCH_ITEMS[i];
    if (item?.status === "processing") startTicker();
  };

  const elapsedSeconds = rep => {
    const values = [
      rep?.metrics?.elapsed_seconds,
      typeof DOC !== "undefined" ? DOC?.metrics?.elapsed_seconds : null,
    ];
    return values.map(Number).find(value => Number.isFinite(value) && value > 0) || 0;
  };

  const elapsedBadge = rep => {
    if (typeof BATCH_MODE !== "undefined" && BATCH_MODE) return null;
    const seconds = elapsedSeconds(rep);
    if (!(seconds > 0)) return null;
    const badge = document.createElement("span");
    badge.className = "single-run-time";
    badge.textContent = `Processed in ${runDuration(seconds)}`;
    badge.style.cssText = [
      "display:inline-flex",
      "align-items:center",
      "width:max-content",
      "margin-top:8px",
      "padding:4px 9px",
      "border:1px solid var(--line)",
      "border-radius:999px",
      "background:var(--surface)",
      "color:var(--sage-deep)",
      "font-size:11.5px",
      "font-weight:600",
      "font-variant-numeric:tabular-nums",
    ].join(";");
    return badge;
  };

  const addCertificateTime = rep => {
    const badge = elapsedBadge(rep);
    const heading = document.querySelector("#certificate .cert-inner > h2");
    if (badge && heading && !document.querySelector("#certificate .single-run-time")) {
      heading.insertAdjacentElement("afterend", badge);
    }
  };

  const addAnalysisTime = rep => {
    const badge = elapsedBadge(rep);
    const title = document.querySelector("#dash .report-title > div");
    if (badge && title && !document.querySelector("#dash .single-run-time")) {
      title.appendChild(badge);
    }
  };

  const originalRenderCertificate = renderCertificate;
  renderCertificate = rep => {
    const result = originalRenderCertificate(rep);
    addCertificateTime(rep);
    return result;
  };

  const originalRenderDash = renderDash;
  renderDash = rep => {
    const result = originalRenderDash(rep);
    addAnalysisTime(rep);
    return result;
  };
})();
