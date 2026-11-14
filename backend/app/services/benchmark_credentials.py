"""Separate benchmark history and workload manifests from runtime credentials."""

import re
import shlex
from copy import deepcopy
from dataclasses import dataclass, field
from uuid import UUID

# The kubelet expands these in env.value and argv, but not in Secret data.
_ENV_EXPRESSION = re.compile(r"(\$\$|\$\([A-Za-z_][A-Za-z_0-9]*\))")


def sensitive_name(name: str) -> bool:
    name = name.lower().replace("-", "_")
    words = (
        "api_key",
        "apikey",
        "password",
        "passwd",
        "secret",
        "credential",
        "authorization",
        "access_key",
        "private_key",
    )
    return any(word in name for word in words) or name in {"token", "bearer"} or name.endswith("_token")


def _private_args(argv: list) -> set[int]:
    private = set()
    consume = False
    for i, arg in enumerate(argv):
        if not isinstance(arg, str):
            continue
        if i and argv[i - 1] in ("-c", "-lc"):
            private.add(i)
        if arg.startswith("--"):
            option = re.split(r"[=\s]", arg, maxsplit=1)[0]
            consume = sensitive_name(option[2:])
            if consume and option != arg:
                private.add(i)
        elif consume:
            private.add(i)
        if any(sensitive_name(flag) for flag in re.findall(r"\s--([\w-]+)", arg)):
            private.add(i)
    return private


def public_snapshot(snapshot):
    """Filter both newly written snapshots and pre-upgrade records at the API boundary."""
    if isinstance(snapshot, dict):
        # Copied environment values may be credentials under application-specific names.
        return {
            key: {} if key == "env" else public_snapshot(value)
            for key, value in snapshot.items()
            if key != "legacy_runner_key_encrypted" and not sensitive_name(key)
        }
    if isinstance(snapshot, list):
        private = _private_args(snapshot)
        # External snapshots historically split shell scripts into words. After -c
        # even ordinary positionals can contain shell assignments or embedded secrets.
        for i, arg in enumerate(snapshot):
            if arg in ("-c", "-lc"):
                private.update(range(i + 1, len(snapshot)))
                break
        return ["***" if i in private else public_snapshot(value) for i, value in enumerate(snapshot)]
    return snapshot


@dataclass
class BenchmarkCredentials:
    run_id: UUID
    values: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def name(self):
        return f"bench-{self.run_id.hex}-credentials"

    def env_ref(self, name, key):
        return {"name": name, "valueFrom": {"secretKeyRef": {"name": self.name, "key": key}}}

    def runner_env(self, name, api_key):
        # None references the Secret already created for a deferred runner.
        if isinstance(api_key, dict):
            return {"name": name, **api_key}
        if api_key is not None:
            if api_key in ("", "EMPTY"):
                return {"name": name, "value": api_key}
            self.values["runner-key"] = api_key
        return self.env_ref(name, "runner-key")

    def _literal(self, name, value):
        key = f"value-{len(self.values)}"
        self.values[key] = value
        return self.env_ref(name, key)

    def _private_env(self, name, value, env, names):
        if not _ENV_EXPRESSION.search(value):
            env.append(self._literal(name, value))
            return
        # Preserve Kubernetes expansion order while keeping literal fragments out
        # of the manifest. valueFrom alone would pass "$(OTHER)" verbatim.
        expression = []
        for part in _ENV_EXPRESSION.split(value):
            if not part:
                continue
            if _ENV_EXPRESSION.fullmatch(part):
                expression.append(part)
            else:
                fragment = self._var_name(names)
                env.append(self._literal(fragment, part))
                expression.append(f"$({fragment})")
        env.append({"name": name, "value": "".join(expression)})

    def _var_name(self, names):
        name = f"LLMOPS_BENCH_ARG_{len(self.values)}"
        while name in names:
            name += "_"
        names.add(name)
        return name

    def protect_env(self, items):
        env = []
        names = {item["name"] for item in items}
        for item in items:
            if item.get("value"):
                self._private_env(item["name"], item["value"], env, names)
            else:
                env.append(item)
        return env

    def protect_args(self, argv, env, *, shell):
        private = _private_args(argv)
        names = {item["name"] for item in env}
        result = []
        for i, arg in enumerate(argv):
            if i in private or (shell and _ENV_EXPRESSION.search(arg)):
                name = self._var_name(names)
                self._private_env(name, arg, env, names)
                result.append(f'"${{{name}}}"' if shell else f"$({name})")
            else:
                result.append(shlex.quote(arg) if shell else arg)
        return result

    def protect_container(self, container):
        container = deepcopy(container)
        env = self.protect_env(container.get("env", []))
        command = container.get("command", [])
        args = self.protect_args(command + container.get("args", []), env, shell=False)
        if command:
            container["command"] = args[: len(command)]
        if args[len(command) :]:
            container["args"] = args[len(command) :]
        container["env"] = env
        return container

    def protect_deployment(self, manifest):
        manifest = deepcopy(manifest)
        pod = manifest["spec"]["template"]["spec"]
        for key in ("initContainers", "containers"):
            if pod.get(key):
                pod[key] = [self.protect_container(c) for c in pod[key]]
        return manifest

    def manifest(self):
        if not self.values:
            return None
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "immutable": True,
            "metadata": {"name": self.name, "labels": {"app": "llmops-benchmark"}},
            "stringData": self.values.copy(),
        }
