"""Portal streaming load runner, embedded in each performance Job."""

import asyncio
import json
import math
import os
import random
import statistics
import time
from dataclasses import dataclass

import aiohttp

# Ask the serving tokenizer for valid ordinary token IDs. Sending those IDs to
# /completions avoids decode/re-tokenize drift and chat-template overhead.
VOCAB_SAMPLE = " ".join(
    f"The {color} {animal} walks across the {place}."
    for color in ("blue", "red", "green", "yellow")
    for animal in ("tiger", "horse", "rabbit", "fox")
    for place in ("river", "road", "field", "valley")
)


def prompts(rng, vocab, length, prefix, count):
    for _ in range(count):
        yield prefix + rng.choices(vocab, k=length - len(prefix))


async def counters(session, url="/metrics"):
    # This runner executes in the vLLM image, which provides its metrics parser.
    try:
        from prometheus_client.parser import text_string_to_metric_families

        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
            response.raise_for_status()
            families = text_string_to_metric_families(await response.text())
            values = {"hits": {}, "queries": {}}
            for family in families:
                for sample in family.samples:
                    for key in values:
                        if sample.name.removesuffix("_total") == f"vllm:prefix_cache_{key}":
                            values[key][tuple(sorted(sample.labels.items()))] = sample.value
            return values
    except (aiohttp.ClientError, ValueError, TimeoutError, ImportError):
        return None


def counter_delta(before, after):
    result = {"hits": None, "queries": None, "hit_rate": None}
    if not before or not after:
        return result
    deltas = {}
    for key in ("hits", "queries"):
        if not before[key] or before[key].keys() != after[key].keys():
            return result
        changes = [value - before[key][series] for series, value in after[key].items()]
        if any(not math.isfinite(v) or v < 0 for v in changes):
            return result
        deltas[key] = sum(changes)
    hits, queries = deltas["hits"], deltas["queries"]
    rate = hits / queries if queries and hits <= queries else None
    return {**deltas, "hit_rate": rate}


@dataclass
class Sample:
    inputs: int
    outputs: int
    ttft: float
    e2el: float
    tpot: float | None
    itls: list


async def stream(session, url, payload):
    start = time.perf_counter()
    times = []
    usage = None
    done = False
    async with session.post(
        url, json={**payload, "stream": True, "stream_options": {"include_usage": True}}
    ) as response:
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}: {(await response.text())[:350]}")
        async for line in response.content:
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                done = True
                break
            event = json.loads(data)
            if event.get("error"):
                raise ValueError(str(event["error"])[:350])
            choices = event.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                if (
                    choices[0].get("text")
                    or delta.get("content")
                    or delta.get("reasoning_content")
                    or delta.get("reasoning")
                    or any(t.get("function", {}).get("arguments") for t in (delta.get("tool_calls") or []))
                ):
                    times.append((time.perf_counter() - start) * 1000)
            usage = event.get("usage") or usage
    if not done or not times or not usage or not usage.get("completion_tokens") or not usage.get("prompt_tokens"):
        raise ValueError("Incomplete stream: expected output, token usage and [DONE]")
    outputs = usage["completion_tokens"]
    # Coalesced chunks do not expose an individual inter-token latency.
    itls = [b - a for a, b in zip(times, times[1:])] if outputs == len(times) else []
    return Sample(
        usage["prompt_tokens"],
        outputs,
        times[0],
        (time.perf_counter() - start) * 1000,
        (times[-1] - times[0]) / (outputs - 1) if outputs > 1 and len(times) > 1 else None,
        itls,
    )


async def complete(session, model, tokens, output_len):
    sample = await stream(
        session,
        "/v1/completions",
        dict(model=model, prompt=tokens, max_tokens=output_len, ignore_eos=True, temperature=0),
    )
    if sample.inputs != len(tokens):
        raise ValueError("Unexpected input token count")
    return sample.ttft, sample.outputs


def percentile(values, fraction):
    position = fraction * (len(values) - 1)
    lo = int(position)
    return values[lo] + (position - lo) * (values[min(lo + 1, len(values) - 1)] - values[lo])


async def measure(session, model, workload, inputs, phase):
    # Preparation requests have already finished. Only the measured workers
    # contribute latency, generated tokens, wall time and cache-counter deltas.
    before = await counters(session)
    latencies = []
    generated = 0
    errors = []

    async def worker():
        nonlocal generated
        for tokens in inputs:
            if errors:
                return
            try:
                latency, output = await complete(session, model, tokens, workload["random_output_len"])
            except (aiohttp.ClientError, ValueError, TimeoutError) as exc:
                errors.append(str(exc) or type(exc).__name__)
                return
            latencies.append(latency)
            generated += output

    start = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(min(workload["max_concurrency"], workload["num_prompts"]))))
    elapsed = time.perf_counter() - start
    phase.update(completed=len(latencies), cache=counter_delta(before, await counters(session)))
    if latencies:
        # Linear interpolation matches the percentile convention used by vLLM.
        ordered = sorted(latencies)
        phase.update(
            median_ttft_ms=statistics.median(ordered),
            p99_ttft_ms=percentile(ordered, 0.99),
            output_throughput=generated / elapsed,
        )
    if errors:
        raise ValueError(errors[0])


async def run(session, model, workload, result):
    seed = workload["seed"]
    length = workload["random_input_len"]
    async with session.post(
        "/tokenize", json={"model": model, "prompt": VOCAB_SAMPLE, "add_special_tokens": False}
    ) as response:
        response.raise_for_status()
        vocab = sorted(set((await response.json())["tokens"]))
    if len(vocab) < 2:
        raise ValueError("Tokenizer returned too few distinct tokens")
    warmup = next(prompts(random.Random(f"{seed}:warmup"), vocab, length, [], 1))
    await complete(session, model, warmup, workload["random_output_len"])
    for name in ("individual", "shared"):
        phase = result["phases"][name] = {"completed": 0}
        rng = random.Random(f"{seed}:{name}")
        prefix = rng.choices(vocab, k=workload["prefix_len"]) if name == "shared" else []
        inputs = prompts(rng, vocab, length, prefix, workload["num_prompts"])
        if prefix:
            # Prime the shared prefix with an independent suffix.
            prime = next(prompts(random.Random(f"{seed}:prime"), vocab, length, prefix, 1))
            await complete(session, model, prime, 1)
        await measure(session, model, workload, inputs, phase)
        print("<<<RESULT>>>" + json.dumps(result), flush=True)


def load_tokenizer(tokenizer_name):
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_name,
        **({"revision": os.environ["BENCH_TOKENIZER_REVISION"]} if os.environ.get("BENCH_TOKENIZER_REVISION") else {}),
        **({"local_files_only": True} if os.environ.get("HF_HUB_OFFLINE") == "1" else {}),
        **({"trust_remote_code": True} if os.environ.get("BENCH_VALIDATION_TRUST_REMOTE_CODE") == "1" else {}),
    )
    expected = os.environ.get("BENCH_VALIDATION_TOKENIZER_SHA256")
    if expected:
        import hashlib

        actual = hashlib.sha256(json.dumps(tokenizer.get_vocab(), sort_keys=True).encode()).hexdigest()
        if actual != expected:
            raise ValueError("Measurement tokenizer changed after asset validation")
    return tokenizer


def chat_inputs(tokenizer_name, workload):
    tokenizer = load_tokenizer(tokenizer_name)
    # Each sampled piece comes from ordinary text, not special/undecodable IDs.
    # Server usage is authoritative: the server's chat template also costs tokens.
    vocab = sorted(set(tokenizer.encode(VOCAB_SAMPLE, add_special_tokens=False)))
    rng = random.Random(workload["seed"])
    length = workload["random_input_len"]
    return [
        tokenizer.decode(rng.choices(vocab, k=length), clean_up_tokenization_spaces=False)
        for _ in range(workload["num_prompts"])
    ]


def summarize(samples, duration, goodput):
    duration = max(duration, 1e-9)
    n = len(samples)
    inputs = sum(s.inputs for s in samples)
    outputs = sum(s.outputs for s in samples)
    metrics = dict(
        completed=n,
        duration=duration,
        total_input_tokens=inputs,
        total_output_tokens=outputs,
        request_throughput=n / duration,
        output_throughput=outputs / duration,
        total_token_throughput=(inputs + outputs) / duration,
    )
    for key in ("ttft", "tpot", "e2el", "itl"):
        vals = sorted(v for s in samples for v in (s.itls if key == "itl" else [getattr(s, key)]) if v is not None)
        if vals:
            metrics.update(
                {
                    f"mean_{key}_ms": statistics.mean(vals),
                    f"median_{key}_ms": statistics.median(vals),
                    f"p90_{key}_ms": percentile(vals, 0.90),
                    f"p99_{key}_ms": percentile(vals, 0.99),
                }
            )
    if goodput:
        limits = {k: float(v) for k, v in (item.split(":") for item in goodput.split())}
        metrics["request_goodput"] = (
            sum(all(getattr(s, k) is not None and getattr(s, k) <= v for k, v in limits.items()) for s in samples)
            / duration
        )
    return metrics


def chat_url(base):
    base = base.rstrip("/")
    return base + ("" if base.endswith("/v1") else "/v1") + "/chat/completions"


async def run_performance(session, url, model, workload, texts, result):
    # A request never waits for admission inside its measured latency.
    jobs = iter(texts)
    samples = []
    errors = []
    failed = 0
    rate = workload.get("request_rate")
    pacing = random.Random(workload["seed"])
    start = arrival = time.perf_counter()

    async def worker():
        nonlocal arrival, failed
        for text in jobs:
            if errors:
                return
            if rate:
                due = arrival
                arrival += pacing.expovariate(rate)
                await asyncio.sleep(max(0, due - time.perf_counter()))
                if errors:
                    return
            payload = dict(
                model=model,
                messages=[{"role": "user", "content": text}],
                max_tokens=workload["random_output_len"],
                temperature=0,
            )
            if workload.get("ignore_eos"):
                payload["ignore_eos"] = True
            try:
                samples.append(await stream(session, url, payload))
            except (aiohttp.ClientError, ValueError, TimeoutError) as exc:
                failed += 1
                errors.append(str(exc) or type(exc).__name__)
                return

    await asyncio.gather(*(worker() for _ in range(min(workload["max_concurrency"], len(texts)))))
    metrics = summarize(samples, time.perf_counter() - start, workload.get("goodput"))
    result["metrics"] = dict(metrics, failed=failed, requested=len(texts))
    if errors:
        raise ValueError(errors[0])


def configured_prefix(workload):
    return int(workload["random_input_len"] * workload.get("cache_ratio", 0) / 100)


def synthetic_tokens(workload, vocab):
    length = workload["random_input_len"]
    rng = random.Random(f"{workload['seed']}:measurement")
    prefix = rng.choices(vocab, k=configured_prefix(workload))
    inputs = list(prompts(rng, vocab, length, prefix, workload["num_prompts"]))
    warmup = next(prompts(random.Random(f"{workload['seed']}:warmup"), vocab, length, [], 1))
    prime = next(prompts(random.Random(f"{workload['seed']}:prime"), vocab, length, prefix, 1)) if prefix else None
    return inputs, warmup, prime


async def isolated_counters(session, base):
    # Direct endpoints and shared/multi-pod validation targets cannot attribute
    # engine-wide counters to this run. Do not report a misleading hit rate.
    if os.environ.get("BENCH_ISOLATED") != "1":
        return None
    return await counters(session, base.rstrip("/") + "/metrics")


async def run_synthetic(session, base, model, workload, result):
    token_input = os.environ.get("BENCH_TOKEN_INPUT") == "1"
    tokenizer = None
    if token_input:
        async with session.post(
            base.rstrip("/") + "/tokenize", json={"model": model, "prompt": VOCAB_SAMPLE, "add_special_tokens": False}
        ) as response:
            response.raise_for_status()
            vocab = sorted(set((await response.json())["tokens"]))
    elif workload.get("input_length_mode") == "text_estimate":
        # API model aliases need not be Hugging Face repositories. This offline
        # corpus is sized in words; server usage remains authoritative for tokens.
        vocab = sorted(set(VOCAB_SAMPLE.split()))
    else:
        tokenizer = load_tokenizer(os.environ["BENCH_TOKENIZER"])
        vocab = sorted(set(tokenizer.encode(VOCAB_SAMPLE, add_special_tokens=False)))
    if len(vocab) < 2:
        raise ValueError("Tokenizer returned too few distinct tokens")
    inputs, warmup, prime = synthetic_tokens(workload, vocab)
    url = (
        base.rstrip("/") + ("" if base.rstrip("/").endswith("/v1") else "/v1") + "/completions"
        if token_input
        else chat_url(base)
    )

    async def request(tokens, output_len):
        payload = dict(model=model, max_tokens=output_len, temperature=0)
        if token_input:
            payload["prompt"] = tokens
        else:
            payload["messages"] = [
                {"role": "user", "content": tokenizer.decode(tokens, clean_up_tokenization_spaces=False) if tokenizer else " ".join(tokens)}
            ]
        if workload.get("ignore_eos"):
            payload["ignore_eos"] = True
        sample = await stream(session, url, payload)
        if token_input and sample.inputs != len(tokens):
            raise ValueError("Unexpected input token count")
        return sample

    await request(warmup, 8)
    if prime is not None:
        await request(prime, 1)
    before = await isolated_counters(session, base)
    jobs = iter(inputs)
    samples, errors = [], []
    start = arrival = time.perf_counter()
    pacing = random.Random(workload["seed"])

    async def worker():
        nonlocal arrival
        for tokens in jobs:
            if workload.get("request_rate"):
                due = arrival
                arrival += pacing.expovariate(workload["request_rate"])
                await asyncio.sleep(max(0, due - time.perf_counter()))
            try:
                samples.append(await request(tokens, workload["random_output_len"]))
            except (aiohttp.ClientError, ValueError, TimeoutError) as exc:
                errors.append(str(exc) or type(exc).__name__)

    await asyncio.gather(*(worker() for _ in range(min(workload["max_concurrency"], len(inputs)))))
    duration = time.perf_counter() - start
    cache = counter_delta(before, await isolated_counters(session, base))
    result["metrics"] = {
        **summarize(samples, duration, workload.get("goodput")),
        "requested": len(inputs),
        "failed": len(errors),
        "cache": cache,
    }
    unit = "words" if workload.get("input_length_mode") == "text_estimate" and not token_input else "tokens"
    result["input"] = {
        f"configured_{unit}": workload["random_input_len"],
        f"prefix_{unit}": configured_prefix(workload),
        "cache_ratio": workload.get("cache_ratio", 0),
        "token_input": token_input,
        "length_mode": "token_ids" if token_input else workload.get("input_length_mode", "tokenizer"),
    }
    if errors:
        raise ValueError(errors[0])


async def run_agentic(session, base, model, workload, result):
    # Inputs are frozen snapshots; generated answers never enter the next turn.
    turns = workload["scenario"]["turns"]
    output_len = workload["random_output_len"]
    url = chat_url(base)
    await stream(
        session,
        url,
        dict(model=model, messages=[{"role": "user", "content": "Warm up: say ready."}], max_tokens=8, temperature=0),
    )
    await stream(session, url, dict(model=model, **turns[0], max_tokens=output_len, temperature=0))
    before = await isolated_counters(session, base)
    jobs = iter(range(workload["num_sessions"]))
    samples, errors, sessions = [], [], []
    per_turn = [{"samples": [], "failed": 0, "skipped": 0, "start": None, "end": None} for _ in turns]
    start = arrival = time.perf_counter()
    pacing = random.Random(workload["seed"])

    async def worker():
        nonlocal arrival
        for session_id in jobs:
            if workload.get("request_rate"):
                due = arrival
                arrival += pacing.expovariate(workload["request_rate"])
                await asyncio.sleep(max(0, due - time.perf_counter()))
            began = time.perf_counter()
            completed = 0
            for index, turn in enumerate(turns):
                stats = per_turn[index]
                stats["start"] = stats["start"] or time.perf_counter()
                try:
                    sample = await stream(session, url, dict(model=model, **turn, max_tokens=output_len, temperature=0))
                    samples.append(sample)
                    stats["samples"].append(sample)
                    completed += 1
                except (aiohttp.ClientError, ValueError, TimeoutError) as exc:
                    stats["failed"] += 1
                    errors.append(str(exc) or type(exc).__name__)
                    for skipped in per_turn[index + 1 :]:
                        skipped["skipped"] += 1
                    break
                finally:
                    stats["end"] = time.perf_counter()
            sessions.append(
                {
                    "session": session_id + 1,
                    "completed_turns": completed,
                    "completed": completed == len(turns),
                    "duration_ms": (time.perf_counter() - began) * 1000,
                }
            )

    await asyncio.gather(*(worker() for _ in range(min(workload["max_concurrency"], workload["num_sessions"]))))
    duration = time.perf_counter() - start
    result["metrics"] = {
        **summarize(samples, duration, workload.get("goodput")),
        "requested": workload["num_sessions"] * len(turns),
        "failed": len(errors),
        "skipped": sum(t["skipped"] for t in per_turn),
        "completed_sessions": sum(s["completed"] for s in sessions),
        "requested_sessions": workload["num_sessions"],
        "cache": counter_delta(before, await isolated_counters(session, base)),
    }
    result["turns"] = [
        {
            "turn": i + 1,
            **summarize(t["samples"], (t["end"] - t["start"]) if t["start"] else 0, None),
            "failed": t["failed"],
            "skipped": t["skipped"],
            "requested": workload["num_sessions"],
        }
        for i, t in enumerate(per_turn)
    ]
    result["sessions"] = sorted(sessions, key=lambda s: s["session"])
    result["scenario"] = {k: workload["scenario"][k] for k in ("id", "version", "input_hash")}
    if errors:
        raise ValueError(errors[0])


async def main():
    workload = json.loads(os.environ["BENCH_WORKLOAD"])
    cache = workload.get("experiment") == "prefix_cache"
    result = {"runner": "portal", "runner_version": 2, "experiment": "prefix_cache" if cache else "performance"}
    if cache:
        result["phases"] = {}
    api_key = os.environ.get("BENCH_RUNNER_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    headers = {"Authorization": "Bearer " + api_key} if api_key else {}
    try:
        base = os.environ["BENCH_URL"].rstrip("/")
        model = os.environ["BENCH_MODEL"]
        async with aiohttp.ClientSession(
            base_url=base if cache else None,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=600),
            connector=aiohttp.TCPConnector(limit=0),
        ) as session:
            if cache:
                await run(session, model, workload, result)
            elif workload.get("workload_type") == "agentic_replay":
                await run_agentic(session, base, model, workload, result)
            elif workload.get("workload_version", 1) >= 2:
                await run_synthetic(session, base, model, workload, result)
            else:
                texts = chat_inputs(os.environ["BENCH_TOKENIZER"], workload)
                if os.environ.get("BENCH_VALIDATION_WARMUP") == "1":
                    warm = chat_inputs(
                        os.environ["BENCH_TOKENIZER"],
                        {**workload, "seed": workload["seed"] + 11000003, "num_prompts": 1},
                    )
                    await stream(
                        session,
                        chat_url(base),
                        dict(
                            model=model,
                            messages=[{"role": "user", "content": warm[0]}],
                            max_tokens=8,
                            temperature=0,
                        ),
                    )
                await run_performance(session, chat_url(base), model, workload, texts, result)
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        result["error"] = (message.replace(api_key, "***") if api_key else message)[:500]
        print("<<<RESULT>>>" + json.dumps(result), flush=True)
        raise SystemExit(1)
    print("<<<RESULT>>>" + json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
