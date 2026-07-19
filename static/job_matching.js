(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.WippleJobMatching = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const GENERIC_JOB_WORDS = new Set([
    "the", "and", "phase", "project", "job", "street", "road", "station",
    "plant", "bridge", "building", "center", "centre", "school", "library",
    "garage", "tower", "clinic", "annex", "substation", "retrofit",
  ]);

  function normId(value) {
    return String(value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  }

  function normName(value) {
    return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  }

  function editDistance(a, b) {
    const A = [...a];
    const B = [...b];
    const previous = Array.from({ length: B.length + 1 }, (_, i) => i);
    for (let i = 1; i <= A.length; i++) {
      let left = i;
      let diagonal = i - 1;
      for (let j = 1; j <= B.length; j++) {
        const above = previous[j];
        const current = Math.min(
          above + 1,
          left + 1,
          diagonal + (A[i - 1] === B[j - 1] ? 0 : 1),
        );
        previous[j] = current;
        diagonal = above;
        left = current;
      }
    }
    return previous[B.length];
  }

  function nameSimilarity(a, b) {
    a = normName(a);
    b = normName(b);
    if (!a || !b) return 0;
    if (a === b) return 1;
    const levenshtein = 1 - editDistance(a, b) / Math.max(a.length, b.length);
    const A = new Set(a.split(" "));
    const B = new Set(b.split(" "));
    const shared = [...A].filter((token) => B.has(token)).length;
    const tokenOverlap = shared / Math.max(new Set([...A, ...B]).size, 1);
    return Math.max(0, 0.65 * levenshtein + 0.35 * tokenOverlap);
  }

  function actualName(observation) {
    if (observation.jobName) return observation.jobName;
    // A display label often falls back to the ID. Never compare that fallback
    // as though it were a job name: JOB-123 and JOB-124 are different IDs.
    return observation.jobId ? "" : observation.label || "";
  }

  function identityScore(a, b) {
    const aId = normId(a.jobId);
    const bId = normId(b.jobId);
    const aName = actualName(a);
    const bName = actualName(b);
    const similarity = nameSimilarity(aName, bName);
    let score = similarity;

    if (aId && bId && aId === bId) {
      const namesConflict = a.jobName && b.jobName && similarity < 0.35;
      score = namesConflict ? 0.78 : 1;
    } else if (aId && bId && aId !== bId) {
      // Different IDs prevent an automatic match, but do not hide a strong
      // name candidate. Contractors sometimes change or recycle identifiers.
      score *= 0.92;
    }

    if (normName(a.jobName) && normName(a.jobName) === normName(b.jobName)) {
      score = Math.max(score, aId && bId && aId !== bId ? 0.78 : 0.96);
    }
    return score;
  }

  function plausibleCandidate(a, b) {
    const aId = normId(a.jobId);
    const bId = normId(b.jobId);
    if (aId && bId && aId === bId) return true;

    const aName = normName(actualName(a));
    const bName = normName(actualName(b));
    if (aName && aName === bName) return true;
    if (nameSimilarity(aName, bName) < 0.64) return false;

    const meaningful = new Set(
      aName.split(" ").filter((token) => token.length >= 4 && !GENERIC_JOB_WORDS.has(token)),
    );
    return bName.split(" ").some((token) => meaningful.has(token));
  }

  function isPlausibleIdentityMatch(a, b) {
    return identityScore(a, b) >= 0.60 && plausibleCandidate(a, b);
  }

  return {
    editDistance,
    identityScore,
    isPlausibleIdentityMatch,
    nameSimilarity,
    normId,
    normName,
    plausibleCandidate,
  };
});
