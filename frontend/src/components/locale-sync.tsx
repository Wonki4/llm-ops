"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useMe } from "@/hooks/use-api";

const COOKIE_NAME = "locale";
const COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

function readLocaleCookie(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)locale=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function writeLocaleCookie(locale: string) {
  document.cookie = `${COOKIE_NAME}=${encodeURIComponent(locale)}; path=/; max-age=${COOKIE_MAX_AGE}; SameSite=Lax`;
}

export function LocaleSync() {
  const router = useRouter();
  const { data: me } = useMe();

  useEffect(() => {
    if (!me?.locale) return;
    if (readLocaleCookie() === me.locale) return;
    writeLocaleCookie(me.locale);
    router.refresh();
  }, [me?.locale, router]);

  return null;
}
