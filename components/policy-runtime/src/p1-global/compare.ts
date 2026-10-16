import { canonicalJson, modelView, type P1Observation } from "@rsgcsg/sts2-connector-client/p1-global";

/**
 * Two-Host comparison of P1 observations offered at aligned occurrences.
 * "semantic" mode ignores locale/display text (name/label/description/title/prompt/keyword text),
 * which legitimately differs between a Chinese Reference profile and an English Managed process.
 * Nothing else is normalised: information, catalog kinds, aliases, relations and commitments must match.
 */
const TEXT_KEYS = new Set(["name", "label", "description", "title", "prompt", "act_name", "character_name", "text", "detail"]);

function strip(value: unknown, semantic: boolean): unknown {
  if (Array.isArray(value)) return value.map((item) => strip(item, semantic));
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (semantic && TEXT_KEYS.has(key)) continue;
      if (semantic && key === "keywords") continue;
      out[key] = strip(item, semantic);
    }
    return out;
  }
  return value;
}

export interface FirstDifference { path: string; left: unknown; right: unknown }

function firstDifference(left: unknown, right: unknown, path = "$"): FirstDifference | null {
  if (canonicalJson(left) === canonicalJson(right)) return null;
  if (Array.isArray(left) && Array.isArray(right)) {
    if (left.length !== right.length) return { path: `${path}.length`, left: left.length, right: right.length };
    for (let i = 0; i < left.length; i++) { const d = firstDifference(left[i], right[i], `${path}[${i}]`); if (d) return d; }
  }
  if (left && right && typeof left === "object" && typeof right === "object" && !Array.isArray(left) && !Array.isArray(right)) {
    const keys = [...new Set([...Object.keys(left), ...Object.keys(right)])].sort();
    for (const key of keys) {
      const d = firstDifference((left as Record<string, unknown>)[key], (right as Record<string, unknown>)[key], `${path}.${key}`);
      if (d) return d;
    }
  }
  return { path, left, right };
}

export interface PairReport {
  schema: "sts2.p1/pair-report-1"; mode: "exact" | "semantic";
  compared: number; matching: number; first_divergence: (FirstDifference & { occurrence: number }) | null;
  left_occurrences: number; right_occurrences: number;
}

export function comparePair(left: readonly P1Observation[], right: readonly P1Observation[], mode: "exact" | "semantic"): PairReport {
  const report: PairReport = { schema: "sts2.p1/pair-report-1", mode, compared: 0, matching: 0, first_divergence: null,
    left_occurrences: left.length, right_occurrences: right.length };
  const count = Math.min(left.length, right.length);
  for (let i = 0; i < count; i++) {
    const a = strip(modelView(left[i]!), mode === "semantic"); const b = strip(modelView(right[i]!), mode === "semantic");
    report.compared += 1;
    const difference = firstDifference(a, b);
    if (!difference) { report.matching += 1; continue; }
    report.first_divergence = { occurrence: left[i]!.envelope.occurrence, ...difference };
    break;
  }
  return report;
}
