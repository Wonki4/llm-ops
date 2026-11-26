import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/recipe-throughput-scenario.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { defaultThroughputInputs, parseThroughputInputs, throughputLimits, throughputForCondition } = await import(
  `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`
);

test("the initial throughput scenario matches the portal workload", () => {
  assert.deepEqual(parseThroughputInputs(defaultThroughputInputs), {
    input_tokens: 100_000, output_tokens: 1_000, concurrency: 8, shared_prefix_percent: 90,
  });
});

const cold = { rpm: [30, 100], input_tpm: [3000, 10000], output_tpm: [300, 1000], reused_input_tokens: 0, effective_concurrency: 4 };
const warm = { rpm: [60, 160], input_tpm: [6000, 16000], output_tpm: [600, 1600], reused_input_tokens: 80, effective_concurrency: 8 };
const result = { method: "roofline", status: "estimated", rpm: [30, 160], cache_scenarios: { cold, warm } };

test("each cache condition displays its own rates, with the original envelope still available", () => {
  for (const [condition, rates] of [["cold", cold], ["warm", warm], ["unknown", result]]) {
    const selected = throughputForCondition(result, condition);
    assert.equal(selected.condition, condition);
    assert.equal(selected.canChoose, true);
    assert.equal(selected.rates, rates);
  }
});

test("zero reusable tokens and disabled caching only show the cold workload", () => {
  for (const scenarios of [{ cold }, { cold, warm: { ...warm, reused_input_tokens: 0 } }]) {
    const selected = throughputForCondition({ ...result, cache_scenarios: scenarios }, "warm");
    assert.equal(selected.condition, "cold");
    assert.equal(selected.canChoose, false);
    assert.equal(selected.rates, cold);
  }
});

test("history, legacy API responses and failed estimates keep their original result", () => {
  for (const data of [{ ...result, method: "historical" }, { ...result, cache_scenarios: undefined },
    { ...result, status: "unavailable" }]) {
    const selected = throughputForCondition(data, "warm");
    assert.equal(selected.condition, null);
    assert.equal(selected.canChoose, false);
    assert.equal(selected.rates, data);
  }
});

test("all fields accept the API boundaries and invalidate a partially edited scenario", () => {
  for (const [field, [min, max]] of Object.entries(throughputLimits)) {
    for (const valid of [min, max]) {
      assert.equal(parseThroughputInputs({ ...defaultThroughputInputs, [field]: String(valid) })[field], valid);
    }
    for (const invalid of ["", " ", ".", "1.5", "1e3", String(min - 1), String(max + 1)]) {
      assert.equal(parseThroughputInputs({ ...defaultThroughputInputs, [field]: invalid }), null);
    }
  }
});
