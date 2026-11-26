"""Versioned, offline replay fixtures. Never change an existing version's input."""

import copy
import hashlib
import json


def builtin_scenario():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_docs",
                "description": "Search operational documentation",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_document",
                "description": "Read a document",
                "parameters": {
                    "type": "object",
                    "properties": {"document_id": {"type": "string"}},
                    "required": ["document_id"],
                },
            },
        },
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "You are an operations assistant. Use documentation to explain a safe deployment "
                "rollout. Summarize evidence and list checks. Do not execute changes."
            ),
        },
        {
            "role": "user",
            "content": (
                "Find our model serving rollout procedure, read it, and summarize readiness "
                "checks, traffic switching and rollback."
            ),
        },
    ]
    turns = [{"messages": copy.deepcopy(messages), "tools": tools}]
    messages += [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "search_1",
                    "type": "function",
                    "function": {"name": "search_docs", "arguments": '{"query":"model serving rollout procedure"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "search_1",
            "content": json.dumps(
                {
                    "results": [
                        {
                            "id": "serving-rollout",
                            "title": "Model serving rollout",
                            "excerpt": (
                                "Validate image, model revision, readiness, routing and rollback before publication."
                            ),
                        },
                        {
                            "id": "gpu-capacity",
                            "title": "GPU capacity",
                            "excerpt": "Reserve sufficient GPU capacity before starting a replacement.",
                        },
                    ]
                }
            ),
        },
    ]
    turns.append({"messages": copy.deepcopy(messages), "tools": tools})
    document = "\n".join(
        f"Check {i + 1}: {text}"
        for i, text in enumerate(
            [
                (
                    "Pin the serving image digest, model revision and tokenizer revision. Record the "
                    "applied recipe and GPU allocation."
                ),
                (
                    "Check namespace permissions, storage mounts and available GPU memory. Wait for "
                    "model loading to finish before admitting traffic."
                ),
                (
                    "Verify readiness and run the same benchmark workload on the replacement. Record "
                    "successful requests, output throughput and tail latency."
                ),
                (
                    "Keep the previous serving available during the routing change. Confirm the "
                    "registered model alias resolves to the replacement."
                ),
                (
                    "If requests fail, restore the previous routing target and verify recovery. "
                    "Retain events and logs before cleaning up the failed candidate."
                ),
            ]
            * 12
        )
    )
    messages += [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "read_1",
                    "type": "function",
                    "function": {"name": "read_document", "arguments": '{"document_id":"serving-rollout"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "read_1", "content": document},
        {"role": "user", "content": "Summarize the rollout procedure and rollback checks using this document."},
    ]
    turns.append({"messages": messages, "tools": tools})
    data = {"id": "search_documents", "version": 1, "turns": turns}
    data["input_hash"] = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return data


def pin_scenario(value=None):
    expected = builtin_scenario()
    if value is not None and value != expected:
        raise ValueError("Unknown or modified scenario version; use a supported built-in scenario")
    return expected
