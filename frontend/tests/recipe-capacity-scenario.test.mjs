import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/recipe-capacity-scenario.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { initialDSparkScenario } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);

test("the Kimi DSpark preview starts at the effective decode concurrency", () => {
  const estimate = { pools: [{ role: "aggregated", speculation: { max_num_seqs: 64, scenario_requests: 1 } }] };
  assert.deepEqual(initialDSparkScenario(estimate), { context_tokens: 32768, requests: 64, shared_prefix_tokens: 0 });
});

test("P/D defaults use the decode pool's own override", () => {
  const prefill = { role: "prefill", speculation: { max_num_seqs: 128 } };
  const decode = { role: "decode", speculation: { max_num_seqs: 32 } };
  assert.equal(initialDSparkScenario({ pools: [prefill, decode] }).requests, 32);
  assert.equal(initialDSparkScenario({ pools: [prefill] }), null);
});

test("a built-in DeepSeek DSpark uses the effective recipe limit", () => {
  assert.equal(initialDSparkScenario({ pools: [
    { role: "aggregated", integrated_speculation: { draft_tokens: 5, max_num_seqs: 128 } },
  ] }).requests, 128);
});

test("ordinary recipes and unresolved scheduler limits keep the default scenario", () => {
  assert.equal(initialDSparkScenario({ pools: [{ role: "aggregated", max_num_seqs: 64 }] }), null);
  for (const limit of [null, 0, 1, 1.5, 1_000_001]) {
    assert.equal(initialDSparkScenario({ pools: [{ role: "decode", speculation: { max_num_seqs: limit } }] }), null);
  }
});
