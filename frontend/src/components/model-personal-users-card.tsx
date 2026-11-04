"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ChevronDown, ChevronRight, Loader2, Users } from "lucide-react";

import { useModelPersonalUsers } from "@/hooks/use-api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * Admin view on the model page: the users whose personal-key grant reaches this model —
 * by name, through an access group, or "all". Collapsed until opened (one query then).
 */
export function ModelPersonalUsersCard({ modelName }: { modelName: string }) {
  const t = useTranslations("accessGroups");
  const [open, setOpen] = useState(false);
  const { data: users, isLoading } = useModelPersonalUsers(modelName, open);

  const viaLabel = (via: string) =>
    via === "direct" ? t("viaDirect") : via === "all" ? t("viaAll") : t("viaGroup", { name: via });

  return (
    <Card data-testid="model-personal-users">
      <CardHeader className="pb-3">
        <button type="button" className="flex items-center gap-2 text-left" onClick={() => setOpen((v) => !v)}>
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          <CardTitle className="flex items-center gap-2 text-base"><Users className="size-4" />{t("modelUsersTitle")}</CardTitle>
          {users && <span className="text-sm text-muted-foreground">({users.length})</span>}
        </button>
      </CardHeader>
      {open && (
        <CardContent className="space-y-2">
          <p className="text-xs text-muted-foreground">
            {t("modelUsersHint")} <Link href="/admin/access-groups" className="text-primary hover:underline">{t("manageGroups")}</Link>
          </p>
          {isLoading ? (
            <div className="flex justify-center py-4"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
          ) : !users?.length ? (
            <p className="py-4 text-center text-sm text-muted-foreground">{t("modelUsersEmpty")}</p>
          ) : (
            <div className="max-h-80 divide-y overflow-y-auto rounded-md border">
              {users.map((u) => (
                <div key={u.user_id} className="flex items-center gap-3 px-3 py-1.5 text-sm">
                  <Link href={`/admin/users/${encodeURIComponent(u.user_id)}`} className="font-mono text-xs hover:underline">{u.user_id}</Link>
                  <span className="truncate text-muted-foreground">{u.display_name ?? u.email ?? ""}</span>
                  {!u.logged_in && <Badge variant="outline" className="text-amber-600">{t("notSignedIn")}</Badge>}
                  <span className="ml-auto flex gap-1">
                    {u.via.map((v) => <Badge key={v} variant="secondary" className="font-mono text-[11px]">{viaLabel(v)}</Badge>)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}
