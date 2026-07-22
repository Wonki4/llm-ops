"use client";

import { useEffect, useRef, useState } from "react";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/**
 * A two-line-clamped description that reveals its full text in a hover tooltip,
 * but only when the text is actually truncated. Keeps the same clamped `<p>`
 * element mounted across truncation changes so the overflow observer stays
 * attached.
 */
export function ClampedDescription({
  text,
  className = "",
}: {
  text: string;
  className?: string;
}) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [truncated, setTruncated] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const check = () => setTruncated(el.scrollHeight > el.clientHeight + 1);
    check();
    const observer = new ResizeObserver(check);
    observer.observe(el);
    return () => observer.disconnect();
  }, [text]);

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <p
            ref={ref}
            className={`line-clamp-2 text-sm text-muted-foreground ${className}`.trim()}
          >
            {text}
          </p>
        </TooltipTrigger>
        {truncated && (
          <TooltipContent className="max-w-xs whitespace-pre-line">
            {text}
          </TooltipContent>
        )}
      </Tooltip>
    </TooltipProvider>
  );
}
