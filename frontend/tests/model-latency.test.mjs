import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/model-latency.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
});
const { appendLatencySample, combineLatencySamples, formatLatency, latencyAxis, latencyKey, latencyPeak, latencySegments, LATENCY_HISTORY_MS } =
  await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

function sample(at, value) { return { at, ttft: value, e2e: value, itl: value }; }

test("two clusters or pools using the same Kubernetes names get separate chart traces", () => {
  const target = { cluster_id: "a", cluster_name: "GPU", namespace: "serving", deployment_name: "chat", pool: "decode", engine: "vllm" };
  assert.notEqual(latencyKey(target), latencyKey({ ...target, cluster_id: "b" }));
  assert.notEqual(latencyKey(target), latencyKey({ ...target, pool: "prefill" }));
  assert.equal(latencyKey(target), latencyKey({ ...target, shared: true }));
});

test("live polls take precedence over overlapping backfill, with a bounded time range", () => {
  const data = combineLatencySamples(
    [sample(0, 1), sample(30, 2), sample(60, 3)], [sample(60, 0), sample(75, 4), sample(90, 5)], 25, 80,
  );
  assert.deepEqual(data, [sample(30, 2), sample(60, 0), sample(75, 4)]);
  const recent = appendLatencySample([sample(0, 1), sample(LATENCY_HISTORY_MS - 15000, 2)], sample(LATENCY_HISTORY_MS + 1, 0), 15000);
  assert.equal(recent.length, 2);
  assert.equal(recent.at(-1).ttft, 0);
});

test("missing samples and time gaps split lines, preserving a measured zero", () => {
  const history = [sample(0, 0), sample(30, 10), sample(60, null), sample(90, 20), sample(150, 15)];
  assert.deepEqual(latencySegments(history, "ttft", 45).map(piece => piece.map(p => p.value)), [[0, 10], [20], [15]]);
  const afterMissedPoll = appendLatencySample([sample(0, 2)], sample(32, 5), 15);
  assert.deepEqual(latencySegments(afterMissedPoll, "ttft", 45).map(piece => piece.map(p => p.value)), [[2], [5]]);
  const withBackfill = combineLatencySamples([sample(0, 2), sample(30, 4)], afterMissedPoll, 0, 32);
  assert.deepEqual(latencySegments(withBackfill, "ttft", 45).map(piece => piece.map(p => p.value)), [[2, 4, 5]]);
});

test("TTFT and E2E select their own highest timestamp across clusters", () => {
  const east = { samples: [
    { at: 10, ttft: 30_000, e2e: 45_000, itl: 25 },
    { at: 20, ttft: 300, e2e: 120_000, itl: null },
  ] };
  const west = { samples: [
    { at: 10, ttft: 350, e2e: 3000, itl: 30 },
    { at: 20, ttft: 350, e2e: 3000, itl: 30 },
  ] };
  assert.equal(latencyPeak([west, east], "ttft").at, 10);
  assert.equal(latencyPeak([east, west], "e2e").at, 20);
  assert.equal(latencyPeak([west], "ttft").at, 10);
  assert.deepEqual(latencyPeak([{samples: [sample(1, 0)]}], "ttft"), { at: 1, value: 0, aboveRange: false });
  assert.equal(latencyPeak([{samples: [sample(1, null)]}], "ttft"), null);
});

test("inspection identifies an exceeded bucket independently of measured maxima", () => {
  const samples = [sample(10, 20_000), {...sample(20, null), lower_bounds_ms: { ttft: 10_000 }}];
  assert.deepEqual(latencyPeak([{samples}], "ttft"), { at: 20, value: 10_000, aboveRange: true });
  assert.deepEqual(latencySegments(samples, "ttft", 30).map(segment => segment.map(p => p.at)), [[10]]);
});

test("auto keeps normal traffic visible alongside a large TTFT or E2E spike", () => {
  for (const [baseline, spike] of [[750, 30_000], [90, 125_000]]) {
    const traces = [[baseline, baseline * 1.1], [baseline, baseline, spike]];
    const axis = latencyAxis(traces);
    assert.equal(axis.kind, "log");
    assert.equal(axis.fraction(0), 0);
    assert.ok(axis.fraction(baseline) > 0.2);
    assert.ok(axis.fraction(spike) <= 1 && axis.fraction(spike) > axis.fraction(baseline));
    assert.ok(axis.ticks.at(-1) >= spike);
    assert.deepEqual(axis.ticks, [...new Set(axis.ticks)].sort((a, b) => a - b));
    for (let i = 1; i < axis.ticks.length; i++) {
      assert.ok(axis.fraction(axis.ticks[i]) - axis.fraction(axis.ticks[i - 1]) >= 0.15);
    }
    const linear = latencyAxis(traces, "linear");
    assert.equal(linear.kind, "linear");
    assert.ok(Math.abs(linear.fraction(spike) / linear.fraction(baseline) - spike / baseline) < 1e-8);
  }
});

test("ordinary ranges and a single noisy near-zero sample keep the automatic linear axis", () => {
  assert.equal(latencyAxis([[0, 0], []]).kind, "linear");
  assert.equal(latencyAxis([[1e-8, 500, 520, 600]]).kind, "linear");
  assert.equal(latencyAxis([[0, 100, 110], [750, 800]]).kind, "linear");
  assert.equal(latencyAxis([[10, 12, 11], [750, 800]]).kind, "log");
  const axis = latencyAxis([[0, 1, 1.25], [105, 110]], "log");
  assert.equal(axis.fraction(axis.floor / 2), axis.fraction(axis.floor) / 2);
  assert.ok(Math.abs((axis.fraction(10) - axis.fraction(1)) - (axis.fraction(100) - axis.fraction(10))) < 1e-10);
});

test("zero, sub-millisecond and large finite bounds fit either selected scale", () => {
  for (const [low, high] of [[0, 0], [0.002, 0.8], [Number.MIN_VALUE, Number.MAX_VALUE]]) {
    for (const mode of ["linear", "log", "auto"]) {
      const axis = latencyAxis([[low, high]], mode);
      if (mode !== "auto") assert.equal(axis.kind, mode);
      for (const value of [0, low, high, ...axis.ticks]) {
        assert.ok(Number.isFinite(axis.fraction(value)) && axis.fraction(value) >= 0 && axis.fraction(value) <= 1);
      }
    }
  }
});

test("linear minute axes cover one, five and ten minutes and continue past ten", () => {
  for (const minutes of [1, 5, 10]) {
    const ms = minutes * 60_000;
    const axis = latencyAxis([[350, ms]], "linear");
    assert.deepEqual(axis.ticks, [0, ms / 2, ms]);
    assert.equal(axis.fraction(ms), 1);
  }
  assert.equal(latencyAxis([[59_999]], "linear").ticks.at(-1), 60_000);
  const tenMinutes = 600_000;
  const roundoff = tenMinutes + 1e-10;
  const automatic = latencyAxis([[350, roundoff]], "auto");
  assert.equal(automatic.kind, "log");
  assert.ok(automatic.ticks.at(-1) >= roundoff && automatic.ticks.at(-1) < tenMinutes + 1);
  assert.ok(automatic.ticks.includes(60_000));
  assert.equal(automatic.fraction(roundoff), 1);
  assert.ok(automatic.fraction(350) > 0.2);
  const longer = latencyAxis([[350, tenMinutes + 1]], "linear");
  assert.equal(longer.ticks.at(-1), 720_000);
  assert.ok(longer.fraction(tenMinutes + 1) < 1);
});

test("elapsed latency uses minutes and seconds up to and beyond ten minutes", () => {
  for (const [milliseconds, korean, english] of [
    [350, "350 ms", "350 ms"], [7500, "7.5 s", "7.5 s"], [60_000, "1분", "1 min"],
    [119_300, "1분 59.3초", "1 min 59.3 sec"], [300_000, "5분", "5 min"],
    [599_000, "9분 59초", "9 min 59 sec"], [599_999.99, "9분 59.9초", "9 min 59.9 sec"],
    [600_000, "10분", "10 min"], [600_100, "10분 0.1초", "10 min 0.1 sec"],
    [721_000, "12분 1초", "12 min 1 sec"],
  ]) {
    assert.equal(formatLatency(milliseconds, "ko"), korean);
    assert.equal(formatLatency(milliseconds, "en"), english);
  }
});

test("numerical interpolation noise is distinct from a genuine near-threshold latency", () => {
  assert.equal(formatLatency(600_000 - 1e-10, "ko"), "10분");
  assert.equal(formatLatency(600_000 - 1e-6, "ko"), "9분 59.9초");
  assert.equal(formatLatency(59_999.999, "ko"), "59.99 s");
  assert.equal(formatLatency(999.999, "en"), "999.9 ms");
  assert.equal(formatLatency(0, "ko"), "0 ms");
});
