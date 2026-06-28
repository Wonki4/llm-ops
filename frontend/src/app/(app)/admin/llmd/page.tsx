"use client";

import Link from "next/link";
import { Loader2, Plus, Trash2, Network, ChevronRight } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useLlmdStacks, useDeleteLlmdStack } from "@/hooks/use-api";
import type { LlmdStackSummary } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

export default function LlmdPage() {
  const t = useTranslations("llmd");
  const { data: stacks, isLoading } = useLlmdStacks();
  const deleteMut = useDeleteLlmdStack();

  const handleDelete = (e: React.MouseEvent, s: LlmdStackSummary) => {
    e.preventDefault();
    e.stopPropagation();
    if (!window.confirm(t("deleteConfirm", { name: s.name }))) return;
    deleteMut.mutate(s.id, {
      onSuccess: () => toast.success(t("deleteSuccess")),
      onError: (err) => toast.error(err instanceof Error ? err.message : t("deleteFailed")),
    });
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle className="text-base flex items-center gap-2"><Network className="size-4" />{t("title")}</CardTitle>
            <CardDescription>{t("description")}</CardDescription>
          </div>
          <Link href="/admin/llmd/new">
            <Button size="sm"><Plus className="size-4" />{t("addButton")}</Button>
          </Link>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex justify-center py-8"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
        ) : stacks && stacks.length > 0 ? (
          <div className="space-y-2">
            {stacks.map((s) => (
              <Link
                key={s.id}
                href={`/admin/llmd/${s.id}`}
                className="flex items-center justify-between gap-3 rounded-md border p-3 transition-colors hover:border-primary hover:bg-muted/40"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium truncate">{s.name}</span>
                    <Badge variant="secondary">{t("syncLabel")}: {s.sync_status}</Badge>
                    <Badge variant={s.health_status === "Healthy" ? "default" : "secondary"}>
                      {t("healthLabel")}: {s.health_status}
                    </Badge>
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5 space-x-2 font-mono">
                    <span>→ {s.target_model_name}</span><span>ns: {s.namespace}</span>
                  </div>
                  {s.status_message && <p className="text-xs text-muted-foreground mt-1">{s.status_message}</p>}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    className="text-destructive hover:text-destructive"
                    onClick={(e) => handleDelete(e, s)}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                  <ChevronRight className="size-4 text-muted-foreground" />
                </div>
              </Link>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{t("empty")}</p>
        )}
      </CardContent>
    </Card>
  );
}
