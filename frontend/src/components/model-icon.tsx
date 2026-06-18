"use client";

import { useState } from "react";

import { resolveModelIcon } from "@/lib/model-icons";

/**
 * Small model icon: explicit catalog `iconUrl` wins, otherwise the provider
 * logo (from litellm_provider). Renders nothing if there's no icon or if the
 * image fails to load — callers can always show the model name alongside it.
 */
export function ModelIcon({
  iconUrl,
  provider,
  modelName,
  size = 20,
  className = "",
}: {
  iconUrl?: string | null;
  provider?: string | null;
  modelName?: string | null;
  size?: number;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const src = resolveModelIcon(iconUrl, provider, modelName);
  if (!src || failed) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt=""
      width={size}
      height={size}
      style={{ width: size, height: size }}
      className={`shrink-0 rounded object-contain ${className}`}
      onError={() => setFailed(true)}
    />
  );
}
