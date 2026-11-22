import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/serving-scope.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { scopeFromParams, scopeQuery, inScope, namespacesIn } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const settings = { default_namespace: "portal", clusters: [
  { id: "A", namespace: "a-serving", is_default: true },
  { id: "B", namespace: "b-serving", is_default: false },
] };

test("explicit non-default cluster uses its namespace and survives a link round trip", () => {
  const scope = scopeFromParams(new URLSearchParams("cluster=B"), settings);
  assert.deepEqual(scope, { cluster: "B", ns: "b-serving" });
  assert.deepEqual(scopeFromParams(new URLSearchParams(scopeQuery(scope, true)), settings), scope);
  assert.equal(inScope(scope, { cluster_id: "B", namespace: "b-serving" }), true);
  assert.equal(inScope(scope, { cluster_id: "A", namespace: "b-serving" }), false);
  assert.deepEqual(namespacesIn([{ cluster_id: "A", namespace: "a-serving" }, { cluster_id: "B", namespace: "b-serving" }], "B"), ["b-serving"]);
});
test("omitted scope chooses defaults while explicit all remains all", () => {
  assert.deepEqual(scopeFromParams(new URLSearchParams(), settings), { cluster: "A", ns: "a-serving" });
  assert.deepEqual(scopeFromParams(new URLSearchParams("cluster=&ns="), settings), { cluster: "", ns: "" });
});
