"""Fixed load presets for benchmark sweeps — the portal's measurement methodology.

Every sweep combo runs under exactly one preset so results stay comparable
across runs, models and time. Preset params are copied into run.params at
submit (plus {"preset": key}), so historical runs keep their actual
conditions even if these numbers are retuned later.
"""

LOAD_PRESETS: dict[str, dict] = {
    # short interactive chat
    "chat": {
        "random_input_len": 512, "random_output_len": 256,
        "num_prompts": 300, "max_concurrency": 32,
    },
    # RAG / document summarization
    "long_input": {
        "random_input_len": 4096, "random_output_len": 512,
        "num_prompts": 120, "max_concurrency": 8,
    },
    # generation-heavy
    "long_output": {
        "random_input_len": 256, "random_output_len": 1024,
        "num_prompts": 200, "max_concurrency": 16,
    },
}

_COMMON = {"seed": 0, "ignore_eos": True}


def preset_params(key: str) -> dict:
    """Expanded run params for a preset; raises KeyError on an unknown key."""
    return {**LOAD_PRESETS[key], **_COMMON, "preset": key}
