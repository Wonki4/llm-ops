"use client";
import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

function Redirect({href, tab}: {href: string; tab?: string}) {
  const router = useRouter();
  const params = useSearchParams();
  useEffect(() => {
    const query = new URLSearchParams(params.toString());
    if (tab) query.set("tab", tab);
    router.replace(`${href}${query.size ? `?${query}` : ""}${window.location.hash}`);
  }, [href, tab, params, router]);
  return <div className="h-32 animate-pulse rounded bg-muted" aria-busy="true" />;
}
export function LegacyRedirect(props: {href: string; tab?: string}) {
  return <Suspense><Redirect {...props} /></Suspense>;
}
