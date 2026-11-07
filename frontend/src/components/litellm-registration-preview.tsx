"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { apiFetch } from "@/lib/api";

type Registration = { model_name: string; litellm_model: string; api_base: string };

/** Keep each response tied to the exact draft that requested it, including during debounce. */
export function useRegistrationPreview(path: string, draft: object | null) {
  const [attempt, setAttempt] = useState(0);
  const body = draft ? JSON.stringify(draft) : null;
  const [result, setResult] = useState<{ body: string; data?: Registration; error?: string } | null>(null);
  useEffect(() => {
    if (!body) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const data = await apiFetch<Registration>(path, { method: "POST", body, signal: controller.signal });
        if (!controller.signal.aborted) setResult({ body, data });
      } catch (e) {
        if (!controller.signal.aborted) setResult({ body, error: e instanceof Error ? e.message : String(e) });
      }
    }, 300);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [path, body, attempt]);
  const current = result?.body === body ? result : null;
  return {
    active: !!body,
    data: current?.data,
    error: current?.error,
    retry: () => { setResult(null); setAttempt((n) => n + 1); },
  };
}

export function RegistrationPreview({ preview }: { preview: ReturnType<typeof useRegistrationPreview> }) {
  const t = useTranslations("adminDeployments");
  const common = useTranslations("common");
  if (!preview.active) return null;
  const { data, error } = preview;
  return (
    <section aria-live="polite" className="space-y-2 rounded-md border bg-muted/30 p-3 text-xs">
      <h4 className="font-medium">{t("registrationPreview")}</h4>
      {data ? (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
          {Object.entries({ model_name: data.model_name, provider: data.litellm_model.split("/", 1)[0], model: data.litellm_model, api_base: data.api_base }).map(([key, value]) => (
            <div key={key} className="contents"><dt className="text-muted-foreground">{key}</dt><dd className="break-all font-mono">{value}</dd></div>
          ))}
        </dl>
      ) : error ? (
        <div className="space-y-1">
          <p className="text-destructive">{error}</p>
          <button type="button" onClick={preview.retry} className="underline">{common("retry")}</button>
        </div>
      ) : <p className="text-muted-foreground">{t("registrationLoading")}</p>}
    </section>
  );
}
