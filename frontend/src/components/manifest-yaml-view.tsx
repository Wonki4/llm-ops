"use client";

import { useState } from "react";
import { Copy } from "lucide-react";
import { Tabs } from "radix-ui";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { copyText } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import type { ManifestPreview } from "@/types";

export function ManifestYamlView({ manifests, fill, refreshing }: {
  manifests: ManifestPreview[]; fill?: boolean; refreshing?: boolean;
}) {
  const t = useTranslations("servingRecipes");
  const [selected, setSelected] = useState("");
  const shown = manifests.find((m) => `${m.kind}/${m.name}` === selected) ?? manifests[0];
  if (!shown) return null;

  async function copy(text: string) {
    if (await copyText(text)) toast.success(t("previewCopied"));
    else toast.error(t("previewCopyFailed"));
  }

  return (
    <Tabs.Root value={`${shown.kind}/${shown.name}`} onValueChange={setSelected}
      className={cn("flex min-w-0 flex-col gap-2", fill && "min-h-0 flex-1")}>
      <Tabs.List aria-label="YAML" className="flex flex-wrap gap-1.5">
        {manifests.map((m) => {
          const key = `${m.kind}/${m.name}`;
          return (
            <Tabs.Trigger key={key} value={key}
              className={cn("max-w-full break-all rounded-md border px-2 py-1 text-left font-mono text-xs",
                m === shown ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}>
              {key}
            </Tabs.Trigger>
          );
        })}
      </Tabs.List>
      <div className="flex flex-wrap justify-end gap-1">
        <Button type="button" size="xs" variant="ghost" onClick={() => copy(shown.yaml)}>
          <Copy className="size-3.5" />{t("previewCopy")}
        </Button>
        <Button type="button" size="xs" variant="outline" onClick={() => copy(manifests.map((m) => m.yaml).join("---\n"))}>
          <Copy className="size-3.5" />{t("previewCopyAll")}
        </Button>
      </div>
      <Tabs.Content asChild value={`${shown.kind}/${shown.name}`}>
        <pre className={cn(
          "min-w-0 overflow-auto rounded-md border bg-muted/40 p-3 font-mono text-xs leading-relaxed",
          fill ? "min-h-0 flex-1" : "max-h-[55vh]", refreshing && "opacity-60",
        )}><code>{shown.yaml}</code></pre>
      </Tabs.Content>
    </Tabs.Root>
  );
}
