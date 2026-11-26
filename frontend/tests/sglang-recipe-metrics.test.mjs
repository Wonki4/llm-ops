import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";

async function moduleUrl(path, imports = {}) {
  const source = await readFile(new URL(path, import.meta.url), "utf8");
  let js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  for (const [name, url] of Object.entries(imports)) js = js.replace(JSON.stringify(name), JSON.stringify(url));
  return `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`;
}

const engines = await moduleUrl("../src/lib/serving-engines.ts");
const { DEFAULT_ENGINE_ARGS, configuredEngineFlags } = await import(engines);
const { normalizeDraft } = await import(await moduleUrl("../src/lib/recipe-drafts.ts", { "@/lib/serving-engines": engines }));

test("SGLang launch settings show metrics enabled for both a new recipe and an older recipe", () => {
  for (const args of [DEFAULT_ENGINE_ARGS.sglang, null]) {
    assert.deepEqual(configuredEngineFlags("sglang", args, null), ["--enable-metrics"]);
  }
  assert.deepEqual(configuredEngineFlags("vllm", null, null), []);
});

test("editing a stored SGLang recipe preserves the explicit metrics opt-out", () => {
  const { input, relocated } = normalizeDraft({ engine: "sglang", engine_args: { "enable-metrics": false } });
  assert.deepEqual(input.engine_args, { "enable-metrics": false });
  assert.deepEqual(relocated, []);
  assert.deepEqual(configuredEngineFlags(input.engine, input.engine_args, input.vllm_extra_args), []);
});

test("a manual metrics flag wins and appears once in the rendered launch arguments", () => {
  const args = { "enable-metrics": false, "tp-size": 2 };
  const extra = ["--enable-metrics", "--log-level=info", "--enable-metrics"];
  assert.deepEqual(configuredEngineFlags("sglang", args, extra), ["--enable-metrics", "--tp-size", "2", "--log-level=info"]);
});
