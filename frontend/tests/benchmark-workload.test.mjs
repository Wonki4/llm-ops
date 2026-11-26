import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";
const source = await readFile(new URL("../src/lib/benchmark-workload.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { DEFAULT_PERF_PARAMS: defaults, applyPreset, restoreWorkload, matchedPresetKey, workloadParams, endpointWorkloadParams, requestCount } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);

test("90% default is editable without changing saved presets", () => {
  const preset = { name: "Long", random_input_len: 4096, random_output_len: 512, num_prompts: 120, max_concurrency: 8, cache_ratio: 90 };
  const p = applyPreset(defaults, preset);
  assert.equal(p.cache_ratio, 90);
  p.cache_ratio = 60;
  assert.equal(preset.cache_ratio, 90);
  assert.equal(workloadParams(p).cache_ratio, 60);
  assert.equal(matchedPresetKey({ long: preset }, p), undefined);
});
test("legacy runs restore their actual ratio instead of receiving 90%", () => {
  assert.equal(restoreWorkload({}).cache_ratio, 0);
  const p = restoreWorkload({ experiment: "prefix_cache", prefix_len: 3072, random_input_len: 4096, num_prompts: 120 });
  assert.equal(p.cache_ratio, 75);
  assert.equal(requestCount(p), 120);
  assert.equal(workloadParams(p).experiment, undefined);
});
test("Agentic replay retains its pinned fixture and uses conversation concurrency", () => {
  const scenario = { id: "search_documents", version: 1, input_hash: "hash" };
  const p = restoreWorkload({ workload_type: "agentic_replay", num_sessions: 40, max_concurrency: 8, scenario });
  assert.equal(requestCount(p), 120);
  const params = workloadParams(p);
  assert.equal(params.ignore_eos, false);
  assert.equal(params.cache_ratio, 0);
  assert.deepEqual(params.scenario, scenario);
  assert.equal(params.workload_version, 2);
});
test("URL reruns retain visible load settings without reviving removed overrides", () => {
  const p = restoreWorkload({ num_prompts: 30, random_input_len: 2048, cache_ratio: 75,
    request_rate: 0.5, seed: 42, ignore_eos: true, tokenizer: "/old/tokenizer", goodput: "ttft:10" });
  const params = endpointWorkloadParams(p);
  assert.equal(params.request_rate, 0.5);
  assert.equal(params.cache_ratio, 75);
  assert.equal(params.num_prompts, 30);
  assert.equal(params.random_input_len, 2048);
  for (const key of ["seed", "ignore_eos", "tokenizer", "goodput"]) assert.equal(key in params, false);
  // Recipe validation still uses its explicit comparison settings.
  assert.equal(workloadParams(p).seed, 42);
  assert.equal(workloadParams(p).ignore_eos, true);
  assert.equal(endpointWorkloadParams(defaults).request_rate, undefined);
});
