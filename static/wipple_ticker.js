(() => {
  const PHRASES = ["Watch me wipple", "now watch me nay nayle"];
  const INTERVAL_MS = 2500;
  const FADE_MS = 220;
  let phraseIndex = 0;
  let ticker = null;
  let fadeSwap = null;

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

  const phraseElements = () => {
    const lanes = [...document.querySelectorAll("#batchLanes .lane.processing .lane-msg")];
    if (lanes.length) return lanes;
    const single = ensureSingleLine();
    return single ? [single] : [];
  };

  const preparePhraseElement = element => {
    element.style.transition = `opacity ${FADE_MS}ms ease`;
    element.style.willChange = "opacity";
  };

  const writePhrase = (elements, fadeIn = false) => {
    const base = phrase();
    elements.forEach(element => {
      preparePhraseElement(element);
      if (fadeIn) element.style.opacity = "0";
      element.dataset.base = base;
      element.textContent = `${base}...`;
    });
    if (fadeIn) {
      requestAnimationFrame(() => requestAnimationFrame(() => {
        elements.forEach(element => { element.style.opacity = "1"; });
      }));
    } else {
      elements.forEach(element => { element.style.opacity = "1"; });
    }
    if (elements.length === 1 && typeof LATEST_PROGRESS !== "undefined") {
      LATEST_PROGRESS = elements[0];
    }
  };

  const showPhrase = (fadeIn = false) => {
    if (!processingVisible()) return;
    writePhrase(phraseElements(), fadeIn);
  };

  const fadeToNextPhrase = () => {
    if (!processingVisible()) return;
    const elements = phraseElements();
    if (!elements.length) return;
    elements.forEach(element => {
      preparePhraseElement(element);
      element.style.opacity = "0";
    });
    if (fadeSwap !== null) clearTimeout(fadeSwap);
    fadeSwap = setTimeout(() => {
      fadeSwap = null;
      phraseIndex = (phraseIndex + 1) % PHRASES.length;
      showPhrase(true);
    }, FADE_MS);
  };

  const stopTicker = () => {
    if (ticker !== null) clearInterval(ticker);
    if (fadeSwap !== null) clearTimeout(fadeSwap);
    ticker = null;
    fadeSwap = null;
    phraseIndex = 0;
    phraseElements().forEach(element => { element.style.opacity = "1"; });
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
        fadeToNextPhrase();
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

  const originalFinishProgressLine = finishProgressLine;
  finishProgressLine = () => {
    stopTicker();
    return originalFinishProgressLine();
  };

  /* Preserve the exact browser wall clock for the demo badge. The existing
     elapsed_seconds field remains untouched for older batch/footer behavior. */
  const originalAttachClientElapsed = attachClientElapsed;
  attachClientElapsed = (doc, seconds) => {
    const result = originalAttachClientElapsed(doc, seconds);
    if (!(seconds > 0) || !doc) return result;
    const apply = rep => {
      if (!rep || typeof rep !== "object") return;
      rep.metrics = rep.metrics || {};
      rep.metrics.client_elapsed_seconds = seconds;
    };
    apply(doc);
    (doc.tables || []).forEach(table =>
      (table.sections || []).forEach(section => apply(section.report)));
    return result;
  };

  const elapsedSeconds = rep => {
    const values = [
      typeof DOC !== "undefined" ? DOC?.metrics?.client_elapsed_seconds : null,
      rep?.metrics?.client_elapsed_seconds,
      typeof DOC !== "undefined" ? DOC?.metrics?.elapsed_seconds : null,
      rep?.metrics?.elapsed_seconds,
    ];
    return values.map(Number).find(value => Number.isFinite(value) && value > 0) || 0;
  };

  const formatElapsed = seconds => {
    if (seconds < 60) return `${seconds.toFixed(2)}s`;
    const minutes = Math.floor(seconds / 60);
    return `${minutes}m ${(seconds - 60 * minutes).toFixed(2)}s`;
  };

  const elapsedBadge = rep => {
    if (typeof BATCH_MODE !== "undefined" && BATCH_MODE) return null;
    const seconds = elapsedSeconds(rep);
    if (!(seconds > 0)) return null;
    const badge = document.createElement("span");
    badge.className = "single-run-time";
    badge.textContent = `Processed in ${formatElapsed(seconds)}`;
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

  const originalRenderCertificate = renderCertificate;
  renderCertificate = rep => {
    const result = originalRenderCertificate(rep);
    addCertificateTime(rep);
    return result;
  };
})();
