"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { useLocale } from "next-intl";
import { useQueryClient } from "@tanstack/react-query";
import { Languages } from "lucide-react";

import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Locale } from "@/types";

const COOKIE_NAME = "locale";
const COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

const OPTIONS: { value: Locale; label: string }[] = [
  { value: "ko", label: "한국어" },
  { value: "en", label: "English" },
];

function writeLocaleCookie(locale: string) {
  document.cookie = `${COOKIE_NAME}=${encodeURIComponent(locale)}; path=/; max-age=${COOKIE_MAX_AGE}; SameSite=Lax`;
}

export function LanguageSwitcher() {
  const current = useLocale();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [pending, startTransition] = useTransition();

  const change = (next: Locale) => {
    if (next === current || pending) return;
    startTransition(async () => {
      try {
        await apiFetch<{ locale: string }>("/api/me/locale", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ locale: next }),
        });
        writeLocaleCookie(next);
        await queryClient.invalidateQueries({ queryKey: ["me"] });
        router.refresh();
      } catch {
        // Swallow — user can retry. Avoid noisy toast in sidebar footer.
      }
    });
  };

  return (
    <div className="flex items-center gap-1 px-1">
      <Languages className="h-3.5 w-3.5 text-muted-foreground" />
      <div className="flex flex-1 rounded-md border bg-background overflow-hidden">
        {OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            disabled={pending}
            onClick={() => change(opt.value)}
            className={cn(
              "flex-1 px-2 py-1 text-xs font-medium transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
              opt.value === current
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-foreground",
              pending && "opacity-60 cursor-wait",
            )}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}
