"""Side-by-side line diff of rendered manifests (re-apply comparison page)."""

from app.services import manifest_diff as md


def _deployment(image: str, args: list[str]) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "llama"},
        "spec": {"template": {"spec": {"containers": [{"name": "vllm", "image": image, "args": args}]}}},
    }


def _service(name: str = "llama", port: int = 8000) -> dict:
    return {"apiVersion": "v1", "kind": "Service", "metadata": {"name": name}, "spec": {"ports": [{"port": port}]}}


def test_side_by_side_pairs_changed_lines_and_numbers_each_side():
    rows = md.side_by_side("a\nb\nc\n", "a\nB\nc\nd\n")
    assert rows == [
        {"op": "equal", "left": [1, "a"], "right": [1, "a"]},
        {"op": "replace", "left": [2, "b"], "right": [2, "B"]},
        {"op": "equal", "left": [3, "c"], "right": [3, "c"]},
        {"op": "insert", "left": None, "right": [4, "d"]},
    ]


def test_side_by_side_uneven_block_pairs_then_deletes_the_rest():
    rows = md.side_by_side("keep\nx\ny\n", "keep\nz\n")
    assert rows[1:] == [
        {"op": "replace", "left": [2, "x"], "right": [2, "z"]},
        {"op": "delete", "left": [3, "y"], "right": None},
    ]


def test_dump_keeps_a_long_scalar_on_one_line():
    value = "--chat-template " + "word " * 40
    text = md.dump({"args": [value.strip()]})
    assert text.count("\n") == 2  # "args:" and the one item
    assert value.strip() in text


def test_manifest_diffs_match_by_kind_and_name_and_count_changed_rows():
    current = [_deployment("vllm:v0.9", ["--max-model-len=8192"]), _service()]
    proposed = [_deployment("vllm:v0.10", ["--max-model-len=8192"]), _service()]
    out = md.manifest_diffs(current, proposed)
    assert [(m["kind"], m["name"], m["changed"]) for m in out] == [("Deployment", "llama", 1), ("Service", "llama", 0)]
    (changed,) = [r for r in out[0]["rows"] if r["op"] != "equal"]
    assert changed["left"][1].strip() == "image: vllm:v0.9"
    assert changed["right"][1].strip() == "image: vllm:v0.10"
    assert all(r["op"] == "equal" for r in out[1]["rows"])


def test_manifest_diffs_one_sided_manifests_are_all_inserted_or_deleted():
    out = md.manifest_diffs([_service("old")], [_service("new")])
    assert [(m["kind"], m["name"]) for m in out] == [("Service", "old"), ("Service", "new")]
    assert {r["op"] for r in out[0]["rows"]} == {"delete"}
    assert {r["op"] for r in out[1]["rows"]} == {"insert"}
    assert out[0]["changed"] == len(out[0]["rows"])
