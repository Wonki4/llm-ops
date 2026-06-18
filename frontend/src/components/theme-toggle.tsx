"use client";

import { useTheme } from "next-themes";
import { Sun, Moon, Monitor } from "lucide-react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

const OPTIONS = [
  { value: "light", icon: Sun },
  { value: "dark", icon: Moon },
  { value: "system", icon: Monitor },
] as const;

export function ThemeToggle() {
  // next-themes returns `theme === undefined` on the server and the first client
  // render alike, so reading it directly is hydration-safe (no mounted guard needed).
  const { theme, setTheme } = useTheme();
  const t = useTranslations("theme");

  return (
    <div className="flex items-center gap-1 px-1">
      <div className="flex flex-1 overflow-hidden rounded-md border">
        {OPTIONS.map(({ value, icon: Icon }) => {
          const active = theme === value;
          return (
            <button
              key={value}
              type="button"
              aria-label={t(value)}
              title={t(value)}
              aria-pressed={active}
              onClick={() => setTheme(value)}
              className={cn(
                "flex flex-1 items-center justify-center py-1.5 transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                active
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground",
              )}
            >
              <Icon className="size-3.5" />
            </button>
          );
        })}
      </div>
    </div>
  );
}
