"""Run with python -m unittest discover -s bench (requires accuracy image dependencies)."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from accuracy_model import JsonChatStr, PortalModel


class AccuracyModelTests(unittest.TestCase):
    def setUp(self):
        # Chat mode does not need weights or a tokenizer.
        self.model = PortalModel("alias", "http://proxy/v1", None, True, 2, 2)

    def test_likelihood_uses_only_continuation_and_rejects_missing_echo(self):
        self.model.chat = False
        reply = {"index": 0, "logprobs": {
            "token_logprobs": [None, -2, -0.4, -0.5, -9],
            "top_logprobs": [None, {}, {"A": -0.4}, {"B": -0.1}, {}],
        }}
        send = AsyncMock(return_value=[[reply]])
        with patch.object(self.model, "_send", send):
            requests = [(None, [1, 2], [3, 4])]
            self.assertEqual(self.model._loglikelihood_tokens(requests), [(-0.9, False)])
            batches, path = send.await_args.args
            self.assertEqual(path, "/completions")
            self.assertEqual(batches[0]["prompt"], [[1, 2, 3, 4]])
            reply["logprobs"]["token_logprobs"] = [-0.5]
            with self.assertRaisesRegex(ValueError, "echoed log probabilities"):
                self.model._loglikelihood_tokens(requests)

    def test_chat_uses_messages_and_standard_task_generation_settings(self):
        prompt = JsonChatStr('[{"role":"user","content":"Question?"}]')
        req = SimpleNamespace(args=(prompt, {"until": ["END"], "max_gen_toks": 4, "do_sample": False}))
        send = AsyncMock(return_value=[[{"index": 0, "message": {"content": "answer"}}]])
        with patch.object(self.model, "_send", send):
            self.assertEqual(self.model.generate_until([req]), ["answer"])
        batches, path = send.await_args.args
        self.assertEqual(path, "/chat/completions")
        self.assertEqual(batches, [{"messages": [{"role": "user", "content": "Question?"}],
                                   "stop": ["END"], "max_tokens": 4, "temperature": 0}])
        with self.assertRaisesRegex(ValueError, "disable chat mode"):
            self.model.loglikelihood([])
