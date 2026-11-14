"""Portal HTTP adapter for lm-eval. Task prompts, token boundaries and scoring stay in the harness."""

import asyncio
import json
import os

import aiohttp
from lm_eval.models.api_models import JsonChatStr
from lm_eval.models.openai_completions import LocalCompletionsAPI


class PortalModel(LocalCompletionsAPI):
    def __init__(self, model, api, tokenizer, chat, concurrency, batch_size):
        super().__init__(
            model=model,
            base_url=api,
            tokenizer=tokenizer if not chat else None,
            tokenizer_backend=None if chat else "huggingface",
            tokenized_requests=not chat,
            batch_size=batch_size,
            num_concurrent=concurrency,
        )
        self.chat = chat

    async def _send(self, batches, path):
        headers = (
            {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]}
            if os.environ.get("OPENAI_API_KEY")
            else {}
        )
        jobs = iter(enumerate(batches))
        ordered = [None] * len(batches)
        async with aiohttp.ClientSession(
            headers=headers, timeout=aiohttp.ClientTimeout(total=600)
        ) as session:

            async def worker():
                for i, body in jobs:
                    async with session.post(
                        self.base_url + path, json={**body, "model": self.model}
                    ) as response:
                        if response.status != 200:
                            raise ValueError(
                                f"HTTP {response.status}: {(await response.text())[:350]}"
                            )
                        choices = (await response.json())["choices"]
                        ordered[i] = sorted(choices, key=lambda c: c["index"])

            await asyncio.gather(
                *(worker() for _ in range(min(self._concurrent, len(batches))))
            )
        return ordered

    def _loglikelihood_tokens(self, requests, **kwargs):
        if self.chat:
            raise ValueError(
                "Likelihood tasks require /v1/completions with echo and logprobs; disable chat mode"
            )
        inputs = [(ctx, continuation) for _, ctx, continuation in requests]
        batches = [
            {
                "prompt": [
                    ctx + cont for ctx, cont in inputs[i : i + self._batch_size]
                ],
                "max_tokens": 1,
                "echo": True,
                "logprobs": 1,
                "temperature": 0,
            }
            for i in range(0, len(inputs), self._batch_size)
        ]
        replies = asyncio.run(self._send(batches, "/completions"))
        results = []
        for batch, choices in zip(batches, replies):
            if [choice["index"] for choice in choices] != list(
                range(len(batch["prompt"]))
            ):
                raise ValueError("Missing likelihood choices")
            for choice in choices:
                ctx, continuation = inputs[len(results)]
                p = choice.get("logprobs") or {}
                sl = slice(len(ctx), len(ctx) + len(continuation))
                scores, top = (
                    p.get("token_logprobs", [])[sl],
                    p.get("top_logprobs", [])[sl],
                )
                if (
                    not continuation
                    or len(scores) != len(continuation)
                    or len(top) != len(scores)
                    or any(
                        score is None or not options
                        for score, options in zip(scores, top)
                    )
                ):
                    raise ValueError(
                        "Endpoint did not return echoed log probabilities for the continuation"
                    )
                results.append(
                    (
                        sum(scores),
                        all(
                            score >= max(options.values())
                            for score, options in zip(scores, top)
                        ),
                    )
                )
        return results

    def loglikelihood(self, requests, **kwargs):
        if self.chat:
            raise ValueError(
                "Likelihood tasks require /v1/completions with echo and logprobs; disable chat mode"
            )
        return super().loglikelihood(requests, **kwargs)

    def loglikelihood_rolling(self, requests, **kwargs):
        if self.chat:
            raise ValueError(
                "Rolling likelihood requires the completions endpoint; disable chat mode"
            )
        return super().loglikelihood_rolling(requests, **kwargs)

    def generate_until(self, requests, **kwargs):
        batches = []
        for req in requests:
            prompt, raw = req.args
            options = dict(raw)
            stop = options.pop("until", None)
            if isinstance(stop, str):
                stop = [stop]
            if stop:
                options["stop"] = stop
            options.setdefault("max_tokens", options.pop("max_gen_toks", 256))
            options.setdefault("temperature", 0)
            options.pop("do_sample", None)
            if self.chat:
                messages = (
                    json.loads(prompt.prompt)
                    if isinstance(prompt, JsonChatStr)
                    else [{"role": "user", "content": prompt}]
                )
                batches.append({**options, "messages": messages})
            else:
                batches.append({**options, "prompt": prompt})
        replies = asyncio.run(
            self._send(batches, "/chat/completions" if self.chat else "/completions")
        )
        texts = []
        for choices in replies:
            if len(choices) != 1:
                raise ValueError("Expected one generated answer")
            text = choices[0]["message"]["content"] if self.chat else choices[0]["text"]
            if not isinstance(text, str):
                raise ValueError("Expected a generated text answer")
            texts.append(text)
        return texts
