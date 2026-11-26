export const latencyMetrics = ["ttft", "e2e", "itl"] as const;
export type LatencyMetric = (typeof latencyMetrics)[number];

export interface LatencyTarget {
  cluster_id: string | null;
  cluster_name: string;
  namespace: string;
  deployment_name: string;
  engine?: string | null;
  pool?: string | null;
  shared: boolean;
}

export type LatencySample = {
  at: number;
  missedPoll?: boolean;
  lower_bounds_ms?: Partial<Record<LatencyMetric, number>>;
} & Record<LatencyMetric, number | null>;
export const LATENCY_HISTORY_MS = 30 * 60 * 1000;

export function formatLatency(milliseconds: number, locale: string) {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "—";
  // Histogram interpolation can land a few floating-point steps below an exact boundary.
  const nearestMs = Math.round(milliseconds);
  const value = Math.abs(milliseconds - nearestMs) <= 4 * Number.EPSILON * milliseconds ? nearestMs : milliseconds;
  if (value < 60_000) {
    const showSeconds = value >= 1000;
    const amount = showSeconds ? value / 1000 : value;
    const crossesUnit = Number(amount.toPrecision(4)) >= (showSeconds ? 60 : 1000);
    return `${amount.toLocaleString(locale, {
      maximumSignificantDigits: 4, roundingMode: crossesUnit ? "trunc" : "halfExpand",
    })} ${showSeconds ? "s" : "ms"}`;
  }
  const minutes = Math.floor(value / 60_000);
  const seconds = Math.floor((value % 60_000) / 100) / 10;
  const minuteLabel = minutes.toLocaleString(locale, {style: "unit", unit: "minute", unitDisplay: "short"});
  if (seconds === 0) return minuteLabel;
  const secondLabel = seconds.toLocaleString(locale, {style: "unit", unit: "second", unitDisplay: "short"});
  return `${minuteLabel} ${secondLabel}`;
}

export function latencyKey(row: LatencyTarget) {
  return JSON.stringify([row.cluster_id, row.namespace, row.deployment_name, row.engine, row.pool]);
}

export function appendLatencySample(history: LatencySample[], next: LatencySample, interval: number) {
  const kept = history.filter((point) => point.at >= next.at - LATENCY_HISTORY_MS && point.at < next.at);
  const last = kept.at(-1);
  // A missed poll must leave a break even when both adjacent polls succeeded.
  if (last && next.at - last.at > interval * 1.5) {
    kept.push({ at: last.at + interval, missedPoll: true, ttft: null, e2e: null, itl: null });
  }
  kept.push(next);
  return kept;
}

export function combineLatencySamples(history: LatencySample[], recent: LatencySample[], start: number, end: number) {
  const points = new Map<number, LatencySample>();
  const lastBackfill = history.reduce((latest, point) => Math.max(latest, point.at), -Infinity);
  for (const point of [...history, ...recent]) {
    // Returning to the tab backfills samples that arrived while live polling was paused.
    if (point.missedPoll && point.at <= lastBackfill) continue;
    if (Number.isFinite(point.at) && point.at >= start && point.at <= end) points.set(point.at, point);
  }
  return Array.from(points.values()).sort((a, b) => a.at - b.at);
}

// If any median exceeds the histogram range, the true maximum is unknown.
export function latencyPeak(series: { samples: LatencySample[] }[], metric: LatencyMetric) {
  type Peak = { at: number; value: number; aboveRange: boolean };
  let peak: Peak | null = null;
  for (const { samples } of series) {
    for (const sample of samples) {
      const bound = sample.lower_bounds_ms?.[metric];
      const value = bound ?? sample[metric];
      if (value == null || !Number.isFinite(value) || value < 0) continue;
      const aboveRange = bound != null;
      if (peak && peak.aboveRange && !aboveRange) continue;
      if (!peak || aboveRange !== peak.aboveRange || value > peak.value || (value === peak.value && sample.at < peak.at)) {
        peak = { at: sample.at, value, aboveRange };
      }
    }
  }
  return peak;
}

export function latencySegments(samples: LatencySample[], metric: LatencyMetric, maxGap: number) {
  const segments: { at: number; value: number }[][] = [];
  let segment: { at: number; value: number }[] = [];
  for (const point of samples) {
    const value = point[metric];
    if (value === null || !Number.isFinite(value) || value < 0) {
      segment = [];
      continue;
    }
    if (segment.length && point.at - segment[segment.length - 1].at > maxGap) segment = [];
    if (!segment.length) segments.push(segment);
    segment.push({ at: point.at, value });
  }
  return segments;
}

export type LatencyScale = "auto" | "linear" | "log";

const MINUTE_MS = 60_000;

function linearCeiling(maximum: number) {
  if (maximum === 0) return 1;
  if (maximum > 50_000) {
    for (const minutes of [1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 30, 45, 60]) {
      const tick = minutes * MINUTE_MS;
      // Aggregated bucket rates can put a whole-minute value a few ULPs above the tick.
      if (maximum <= tick + 4 * Number.EPSILON * tick) return Math.max(maximum, tick);
    }
  }
  const magnitude = 10 ** Math.floor(Math.log10(maximum));
  for (const multiplier of [1, 2, 4, 5, 8, 10]) {
    const tick = magnitude * multiplier;
    if (Number.isFinite(tick) && tick >= maximum) return tick;
  }
  return maximum;
}

export function latencyAxis(traces: number[][], mode: LatencyScale = "auto") {
  let high = 0;
  let baseline = Infinity;
  for (const values of traces) {
    let lowest = Infinity;
    let secondLowest = Infinity;
    let positiveCount = 0;
    for (const value of values) {
      if (!Number.isFinite(value) || value <= 0) continue;
      positiveCount++;
      high = Math.max(high, value);
      if (value <= lowest) {
        secondLowest = lowest;
        lowest = value;
      } else {
        secondLowest = Math.min(secondLowest, value);
      }
    }
    // One anomalously small observation should not switch the scale of a whole chart.
    baseline = Math.min(baseline, positiveCount >= 3 ? secondLowest : lowest);
  }
  const top = linearCeiling(high);
  if (mode === "linear" || (mode === "auto" && high / baseline < 32)) {
    return {
      kind: "linear" as const,
      floor: 0,
      ticks: [...new Set([0, top / 2, top])],
      fraction: (value: number) => value / top,
    };
  }

  const exponent = Math.min(Math.floor(Math.log10(baseline)), Math.ceil(Math.log10(top)) - 1);
  const floor = 10 ** exponent || baseline;
  // Between zero and the first positive tick use a linear interval. Above it,
  // each decade takes the same height. All traces use this exact transform.
  const height = (value: number) => value <= floor ? value / floor : 1 + Math.log10(value) - Math.log10(floor);
  const total = height(top);
  const fraction = (value: number) => height(value) / total;
  const ticks = [0];
  function addTick(tick: number) {
    const position = fraction(tick);
    if (position - fraction(ticks[ticks.length - 1]) >= 0.16 && position <= 0.84) ticks.push(tick);
  }
  // Keep logarithmic spacing while labelling long windows at 1, 10, ... minutes.
  // The axis still uses one shared transform for both slow and fast clusters.
  for (let power = exponent; power <= Math.floor(Math.log10(top)) && power <= 4; power++) {
    addTick(10 ** power);
  }
  if (top >= MINUTE_MS) {
    const firstMinutePower = Math.max(0, Math.ceil(Math.log10(floor / MINUTE_MS)));
    for (let power = firstMinutePower; power <= Math.floor(Math.log10(top / MINUTE_MS)); power++) {
      addTick(MINUTE_MS * 10 ** power);
    }
  }
  ticks.push(top);
  return { kind: "log" as const, floor, ticks, fraction };
}
