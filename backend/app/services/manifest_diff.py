"""Side-by-side line diff of rendered K8s manifests (the "re-apply from recipe" page).

Both sides come from the same renderer (``build_all``), so only what the apply
would change shows up. Pure functions, no I/O.
"""

from __future__ import annotations

import difflib
from itertools import zip_longest

import yaml


class PlainDumper(yaml.SafeDumper):
    """Writes shared dicts (selector labels) out in full instead of as &id/*id aliases."""

    def ignore_aliases(self, data) -> bool:
        return True


def dump(manifest: dict) -> str:
    """YAML for comparing: one line per scalar however long, so an argv element changes as one line."""
    return yaml.dump(manifest, Dumper=PlainDumper, sort_keys=False, default_flow_style=False, width=1_000_000)


def side_by_side(current: str, proposed: str) -> list[dict]:
    """Rows ``{"op", "left", "right"}``; a side is ``[line_no, text]`` or None.

    ``op`` is equal / replace / delete / insert. A replaced block pairs its lines
    in order; the longer side's leftovers become deletes or inserts.
    """
    a, b = current.splitlines(), proposed.splitlines()
    rows: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        left = [[i + 1, a[i]] for i in range(i1, i2)]
        right = [[j + 1, b[j]] for j in range(j1, j2)]
        if tag == "equal":
            rows += [{"op": "equal", "left": lt, "right": rt} for lt, rt in zip(left, right)]
            continue
        for lt, rt in zip_longest(left, right):
            op = "replace" if lt and rt else ("delete" if lt else "insert")
            rows.append({"op": op, "left": lt, "right": rt})
    return rows


def _key(manifest: dict) -> tuple[str, str]:
    return manifest.get("kind") or "", (manifest.get("metadata") or {}).get("name") or ""


def manifest_diffs(current: list[dict], proposed: list[dict]) -> list[dict]:
    """One entry per manifest, matched by kind + name: current order, then the proposed-only ones.

    ``{"kind", "name", "changed", "rows"}``; ``changed`` counts the rows that are not equal.
    """
    after = {_key(m): m for m in proposed}
    keys = [_key(m) for m in current]
    keys += [k for k in after if k not in set(keys)]
    before = {_key(m): m for m in current}
    out = []
    for kind, name in keys:
        old, new = before.get((kind, name)), after.get((kind, name))
        rows = side_by_side(dump(old) if old else "", dump(new) if new else "")
        out.append({"kind": kind, "name": name, "changed": sum(r["op"] != "equal" for r in rows), "rows": rows})
    return out
