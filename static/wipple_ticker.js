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
})();
