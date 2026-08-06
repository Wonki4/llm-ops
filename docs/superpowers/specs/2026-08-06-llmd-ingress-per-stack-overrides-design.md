# Per-stack llm-d Ingress host + class overrides — Design

**Date:** 2026-08-06
**Status:** Approved (design)

## Goal

Let an admin set the **ingress host** and **ingress class** per llm-d stack from
the UI, overriding the global config defaults (`llmd_ingress_domain` /
`llmd_ingress_class`). This mirrors the existing per-stack chart-source and EPP
image overrides exactly — nullable override columns, a global fallback, and
fields in the stack form's Advanced section.

## Background — verified facts

- **Depends on #218** (llm-d ingress companion, merged to `main`). Today the
  Ingress host is always `f"{argo_app_name}.{settings.effective_ingress_domain}"`
  and the class is always `settings.llmd_ingress_class`; there is no per-stack
  override. This branch adds the overrides.
- **Existing override pattern** (from the chart-source feature): `CustomLlmdStack`
  has nullable columns `chart_repo/chart_name/chart_version` and
  `epp_registry/epp_repository/epp_tag`. `_chart_source`/`_epp_image` resolve
  `stack.col or settings.default`. `_serialize` returns the effective values plus
  a `chart_overrides` block of the raw columns. The form (`new/page.tsx`,
  `[id]/page.tsx`) has an Advanced section with an Input per override, prefilled
  from a `chart-defaults` endpoint, submitted via
  `overrideOrNull(form.x, defaults.x)` (→ null when equal to the default).
- **Manifest builder** `build_llmd_ingress(stack, *, ingress_class, ingress_domain,
  ingress_path)` currently computes `host = f"{argo_app_name}.{ingress_domain}"`
  internally. `_ingress_for(stack)` passes `settings.llmd_ingress_class`,
  `settings.effective_ingress_domain`, `settings.llmd_ingress_path or "/"`.
- **Migration head:** `042_benchmark_labels_drop_sweeps`.
- **Model:** `custom_llmd_stack` — `epp_tag` is the last override column (before
  `created_by`).

## Decisions

1. **Host = a full-host override** (admin types the whole host, e.g.
   `myrouter.corp.internal`). Empty/NULL → the computed default
   `{argo_app_name}.{effective_ingress_domain}`. (Chosen over a per-stack domain:
   more flexible and matches "set the host on the screen".)
2. **Class = a string override.** NULL → the global `settings.llmd_ingress_class`
   (which, when itself empty, omits `ingressClassName` → cluster default).
3. **Same override mechanics as chart/epp** — nullable columns, global fallback,
   `overrideOrNull` submission, `*_overrides` serializer block, Advanced-section
   inputs. No new UX paradigm.

## Architecture

### Data model — `app/db/models/custom_llmd_stack.py` + migration

Add two nullable columns after `epp_tag`:

```python
    ingress_host: Mapped[str | None] = mapped_column(String(253), nullable=True)
    ingress_class: Mapped[str | None] = mapped_column(String(253), nullable=True)
```

New Alembic migration `043_llmd_stack_ingress_overrides` (down_revision
`042_benchmark_labels_drop_sweeps`): `add_column` both (nullable, no
server_default); `downgrade` drops both.

### Manifest builder — `app/services/llmd_manifests.py`

Change `build_llmd_ingress` to take a fully-formed `host` string instead of
`ingress_domain` (the caller now owns host resolution). Everything else is
unchanged:

```python
def build_llmd_ingress(
    stack: CustomLlmdStack,
    *,
    host: str,
    ingress_class: str,
    ingress_path: str,
) -> dict:
    ...
    "host": host,   # was f"{stack.argo_app_name}.{ingress_domain}"
    ...
```

### API — `app/api/llmd.py`

- Resolver helpers (mirroring `_epp_image`):
  ```python
  def _ingress_host(stack: CustomLlmdStack) -> str:
      return stack.ingress_host or f"{stack.argo_app_name}.{settings.effective_ingress_domain}"

  def _ingress_class(stack: CustomLlmdStack) -> str:
      return stack.ingress_class if stack.ingress_class is not None else settings.llmd_ingress_class
  ```
- `_ingress_for(stack)` passes the resolved host/class:
  ```python
  return build_llmd_ingress(
      stack,
      host=_ingress_host(stack),
      ingress_class=_ingress_class(stack),
      ingress_path=settings.llmd_ingress_path or "/",
  )
  ```
- `CreateLlmdStackRequest` / `UpdateLlmdStackRequest`: add
  `ingress_host: str | None = None` and `ingress_class: str | None = None`.
- `create_stack`: set `ingress_host=(body.ingress_host or "").strip() or None`,
  `ingress_class=(body.ingress_class or "").strip() or None` on the model (same
  form as the chart/epp fields). `update_stack`: add both to the field loop that
  already does `setattr(stack, field, val.strip() or None)` for chart/epp fields.
- `_serialize`: change `ingress_host` to the effective host (`_ingress_host(stack)`),
  add `"ingress_class": _ingress_class(stack)`, and add an overrides block:
  ```python
  "ingress_overrides": {
      "ingress_host": stack.ingress_host,
      "ingress_class": stack.ingress_class,
  },
  ```
- `chart_defaults` endpoint: add `"ingress_class": settings.llmd_ingress_class`
  and `"ingress_domain": settings.effective_ingress_domain` (for the form's
  placeholder/hint; host has no fixed default since it depends on the stack name).

### Frontend — `new/page.tsx`, `[id]/page.tsx`, `types/index.ts`, i18n

- `FormState`: add `ingress_host: string`, `ingress_class: string` (default `""`).
- Advanced section: two `Input`s beside the chart/epp fields —
  `ingressHost` (placeholder hint `{name}.{domain}`, "blank = auto") and
  `ingressClass` (placeholder = global default or "cluster default").
- Submit (`buildPayload`): `ingress_host: form.ingress_host.trim() || null`
  (no fixed default to diff against); `ingress_class:
  overrideOrNull(form.ingress_class, chartDefaults?.ingress_class)`.
- `[id]` edit page: initialise both from `ingress_overrides` (like the chart/epp
  fields initialise from `chart_overrides`).
- `types/index.ts`: `CreateLlmdStackRequest` gains `ingress_host?`,
  `ingress_class?`; the stack response type gains `ingress_class` and
  `ingress_overrides: { ingress_host: string | null; ingress_class: string | null }`
  (`ingress_host` already present).
- i18n (`en.json` + `ko.json`, equal key counts): `ingressHostLabel/Hint/Placeholder`,
  `ingressClassLabel/Hint/Placeholder`.

## Non-goals

- No per-stack `path` or per-stack enable/disable toggle (request was host +
  class only; path stays global `llmd_ingress_path`, ingress stays always-created).
- No change to the global-config fallback behaviour or to #218's create/update/
  delete lifecycle wiring.
- No TLS/cert-manager fields.

## Testing

**Backend:**
- `test_llmd_manifests.py`: update existing `build_llmd_ingress` tests to the new
  `host=` signature; assert the passed host is used verbatim.
- `test_llmd.py`: `_ingress_host`/`_ingress_class` resolution (override wins;
  NULL → global default/computed); `_serialize` returns effective host + class +
  `ingress_overrides`; `create_stack` persists the override columns; a stack with
  `ingress_host` set upserts an Ingress whose rule host is the override.
- `test_config.py` unaffected.
- Migration: `043` chains off `042`, up/down symmetric; `alembic upgrade head`
  clean on a scratch DB (or model↔migration column parity check).

**Frontend:** `tsc --noEmit` clean; `npm run lint`; i18n en/ko key parity 0/0.

## Verification

- `cd backend && python -m pytest tests/test_llmd.py tests/test_llmd_manifests.py -q`
  — 0 new failures vs `origin/main` baseline.
- `ruff check app/api/llmd.py app/services/llmd_manifests.py app/db/models/custom_llmd_stack.py`
- Manual: create a stack with a custom `ingress_host` + `ingress_class` →
  `kubectl get ingress -n <ns> <app>-ingress -o yaml` shows the exact host and
  `ingressClassName`; leave them blank → host `{app}.{global-domain}`, class from
  global config; edit the stack to change them → the Ingress is patched.
