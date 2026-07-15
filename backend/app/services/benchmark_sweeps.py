"""Pure helpers for benchmark sweeps: grid expansion, serve-argv merge and
queued-run promotion. Shared by the sweeps API and the reconciler."""

from __future__ import annotations

from itertools import product

from app.services.benchmark_manifests import job_name_for


def expand_combos(variables: list[dict]) -> list[dict]:
    """Cartesian product of the variables' value lists, row-major (the first
    variable varies slowest). Each combo maps flag -> value."""
    flags = [v["flag"] for v in variables]
    return [dict(zip(flags, vals)) for vals in product(*(v["values"] for v in variables))]


def merge_serve_argv(argv: list, combo: dict) -> list:
    """Merge combo flags into a CLI token list (a full serve argv or a bare
    extra-args list): an existing ``--flag value`` or ``--flag=value`` is
    replaced in place; a bare ``--flag`` (no value slot) gets the value
    inserted after it; otherwise the pair is appended. Returns a new list."""
    out = list(argv)
    for flag, value in combo.items():
        for i, tok in enumerate(out):
            if tok == flag:
                # `--flag value` → replace the value; bare `--flag` (next token
                # is another flag, or end of list) → insert the value, never
                # overwrite an unrelated token.
                if i + 1 < len(out) and not str(out[i + 1]).startswith("--"):
                    out[i + 1] = str(value)
                else:
                    out.insert(i + 1, str(value))
                break
            if isinstance(tok, str) and tok.startswith(flag + "="):
                out[i] = f"{flag}={value}"
                break
        else:
            out += [flag, str(value)]
    return out


async def promote_queued_run(k8s, run) -> None:
    """Create the prebuilt Job for a queued sweep combo and flip it to pending.
    The stored manifest is cleared — it embeds the bench API key."""
    await k8s.create_job(run.k8s_namespace, run.queued_job_manifest)
    run.k8s_job_name = job_name_for(run.id)
    run.status = "pending"
    run.queued_job_manifest = None
