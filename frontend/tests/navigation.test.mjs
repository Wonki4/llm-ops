import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/navigation.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext}}).outputText;
const { activeNavigation, visibleNavigation } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);

test("ordinary users keep the eight ungrouped entries", () => {
  const items = visibleNavigation("user", false);
  assert.equal(items.length, 8);
  assert.ok(items.every(item => !item.group));
});
test("team management and serving scopes do not grant portal administration", () => {
  assert.deepEqual(visibleNavigation("team_admin", false).filter(i => i.group).map(i => i.key), ["adminRequests"]);
  assert.deepEqual(visibleNavigation("user", true).filter(i => i.group).map(i => i.key), ["servingHome"]);
});
test("nested and sibling routes select exactly their owning menu", () => {
  const items = visibleNavigation("super_user", false);
  for (const [path, key] of [["/teams/discover", "discoverTeams"], ["/keys/new", "myKeys"],
    ["/admin/serving/topology", "routingGraph"], ["/admin/models/history", "modelLifecycle"],
    ["/admin/llmd/123", "adminDeployments"], ["/admin/benchmarks/validations/123", "adminBenchmarks"]]) {
    assert.equal(activeNavigation(path, items), key, path);
  }
  assert.equal(activeNavigation("/admin/serving/topology", visibleNavigation("user", true)), "servingHome");
});
