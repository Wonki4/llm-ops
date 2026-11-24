import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/access-groups.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { teamModelScope, buildAccessGroupIndex } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const model = (model_name, groups = [], catalog = { visible: true }) => ({
  model_name, catalog, litellm_info: { model_info: { access_groups: groups } },
});
const models = [model("alpha", ["lab", "language"]), model("beta", ["lab"], null), model("gamma"), model("hidden", [], { visible: false })];
const byName = new Map(models.map((m) => [m.model_name, m]));
const index = buildAccessGroupIndex(models, ["unpublished-group"]);
const group = (access_group_name, models) => ({ access_group_name, models });

test("overlapping direct, model-group and unified grants keep every group and each model once", () => {
  const scope = teamModelScope({ models: ["alpha", "lab"], unified_access_groups: [
    group("Research", ["lab"]), group("Review", ["alpha"]), group("Empty", []),
  ] }, index, byName);
  assert.deepEqual(scope.assignedGroups, ["lab", "Research", "Review", "Empty"]);
  assert.deepEqual(scope.rows.map(({ name, groups }) => [name, groups]), [
    ["alpha", ["lab", "language", "Research", "Review"]], ["beta", ["lab", "Research"]],
  ]);
});
test("group with no visible members is shown as a group, deny marker never becomes a model", () => {
  const scope = teamModelScope({ models: ["no-default-models", "unpublished-group"] }, index, byName);
  assert.deepEqual(scope, { assignedGroups: ["unpublished-group"], rows: [] });
  assert.deepEqual(teamModelScope({ models: ["no-default-models"] }, index, byName), { assignedGroups: [], rows: [] });
});
test("unrestricted teams and inherited all-models grants use visible catalog entries", () => {
  for (const team of [{ models: [] }, { models: ["all-proxy-models"] }, {
    models: ["no-default-models"], unified_access_groups: [group("Everyone", ["all-proxy-models"])],
  }]) {
    const { rows } = teamModelScope(team, index, byName);
    assert.deepEqual(rows.map((r) => r.name), ["alpha", "gamma"]);
    if (team.unified_access_groups) assert.ok(rows.every((r) => r.groups.includes("Everyone")));
  }
});
test("assigned group names are independent of the five-model preview", () => {
  const grants = ["one", "two", "three", "four", "five", "lab"];
  const scope = teamModelScope({ models: grants }, index, byName);
  assert.deepEqual(scope.assignedGroups, ["lab"]);
  assert.deepEqual(scope.rows.slice(0, 5).map((r) => r.name), grants.slice(0, 5));
  assert.equal(scope.rows.length, 7);
});
