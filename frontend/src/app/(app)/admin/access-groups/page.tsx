"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Layers, Loader2, Lock, Pencil, Plus, Search, Trash2, UserPlus } from "lucide-react";
import { toast } from "sonner";

import {
  useAccessGroupMembers,
  useAccessGroups,
  useDeleteAccessGroup,
  useRemoveAccessGroupUsers,
} from "@/hooks/use-api";
import type { AccessGroup, AccessGroupCatalogModel } from "@/types";
import { AddUsersDialog, GroupModelsDialog } from "@/components/access-group-dialogs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

/**
 * Model access groups: pick a group on the left; the right shows its models and the
 * users whose personal-key grant names it, with bulk add (search / pasted roster).
 */
export default function AccessGroupsPage() {
  const t = useTranslations("accessGroups");
  const { data, isLoading } = useAccessGroups();
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const groups = data?.groups ?? [];
  const current = groups.find((g) => g.name === selected) ?? groups[0] ?? null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold"><Layers className="size-5" />{t("pageTitle")}</h1>
        <p className="mt-1 text-muted-foreground">{t("pageDescription")}</p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <Card className="h-fit">
          <CardHeader className="pb-3">
            <Button size="sm" className="w-full" onClick={() => setCreating(true)} data-testid="access-group-new">
              <Plus className="size-3.5" />{t("newGroup")}
            </Button>
          </CardHeader>
          <CardContent className="space-y-1 p-2 pt-0">
            {isLoading && <div className="flex justify-center py-6"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>}
            {!isLoading && groups.length === 0 && <p className="px-2 py-6 text-center text-sm text-muted-foreground">{t("noGroups")}</p>}
            {groups.map((g) => (
              <button
                key={g.name}
                type="button"
                onClick={() => setSelected(g.name)}
                className={`w-full rounded-md px-3 py-2 text-left transition-colors ${current?.name === g.name ? "bg-primary/10" : "hover:bg-accent"}`}
                data-testid="access-group-item"
              >
                <div className="flex items-center gap-1.5 font-mono text-sm font-medium">
                  {g.name}
                  {g.locked && <Lock className="size-3 text-muted-foreground" />}
                </div>
                <div className="text-xs text-muted-foreground">{t("groupCounts", { models: g.models.length, users: g.user_count })}</div>
              </button>
            ))}
          </CardContent>
        </Card>

        {current ? (
          <GroupDetail key={current.name} group={current} models={data?.models ?? []} onDeleted={() => setSelected(null)} />
        ) : (
          !isLoading && (
            <Card><CardContent className="py-12 text-center text-sm text-muted-foreground">{t("selectOrCreate")}</CardContent></Card>
          )
        )}
      </div>

      <GroupModelsDialog open={creating} onOpenChange={setCreating} models={data?.models ?? []} onSaved={(name) => setSelected(name)} />
    </div>
  );
}

function GroupDetail({
  group, models, onDeleted,
}: {
  group: AccessGroup;
  models: AccessGroupCatalogModel[];
  onDeleted: () => void;
}) {
  const t = useTranslations("accessGroups");
  const { data: members, isLoading } = useAccessGroupMembers(group.name);
  const remove = useRemoveAccessGroupUsers();
  const del = useDeleteAccessGroup();
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [filter, setFilter] = useState("");
  const memberIds = useMemo(() => new Set((members ?? []).map((m) => m.user_id)), [members]);

  const q = filter.trim().toLowerCase();
  const shown = (members ?? []).filter(
    (m) => !q || [m.user_id, m.display_name, m.email].some((v) => v?.toLowerCase().includes(q)),
  );

  function removeUser(userId: string) {
    if (!window.confirm(t("removeConfirm", { user: userId, name: group.name }))) return;
    remove.mutate({ group: group.name, user_ids: [userId] }, {
      onSuccess: (rows) =>
        rows[0]?.status === "removed" ? toast.success(t("removed", { user: userId })) : toast.error(t("removeFailed")),
      onError: (e) => toast.error(e instanceof Error ? e.message : t("removeFailed")),
    });
  }

  function deleteGroup() {
    if (!window.confirm(t("deleteConfirm", { name: group.name, users: group.user_count }))) return;
    del.mutate(group.name, {
      onSuccess: (r) => {
        toast.success(t("deleted", { name: group.name, users: r.revoked_users }));
        if (r.failed_users.length) toast.error(t("revokeFailedUsers", { users: r.failed_users.join(", ") }));
        onDeleted();
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : t("deleteFailed")),
    });
  }

  return (
    <Card data-testid="access-group-detail">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-2 font-mono text-lg">
            {group.name}
            {group.locked && <Badge variant="outline" className="gap-1 font-sans"><Lock className="size-3" />{t("lockedBadge")}</Badge>}
          </CardTitle>
          <p className="text-xs text-muted-foreground">{t("personalOnlyNote")}</p>
        </div>
        <Button
          size="sm"
          variant="outline"
          className="text-destructive hover:text-destructive"
          onClick={deleteGroup}
          disabled={group.locked || del.isPending}
          title={group.locked ? t("lockedDeleteHint") : undefined}
          data-testid="access-group-delete"
        >
          <Trash2 className="size-3.5" />{t("deleteGroup")}
        </Button>
      </CardHeader>
      <CardContent className="space-y-6">
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">{t("modelsSection", { count: group.models.length })}</h3>
            <Button size="sm" variant="outline" onClick={() => setEditing(true)} data-testid="access-group-edit-models">
              <Pencil className="size-3.5" />{t("editModels")}
            </Button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {group.models.map((m) => (
              <Badge key={m.model_name} variant="secondary" className="gap-1 font-mono" title={m.locked ? t("lockedMemberHint") : undefined}>
                <Link href={`/models/${m.model_name.split("/").map(encodeURIComponent).join("/")}`} className="hover:underline">{m.model_name}</Link>
                {m.locked && <Lock className="size-3" />}
              </Badge>
            ))}
          </div>
        </section>

        <section className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="mr-auto text-sm font-medium">{t("usersSection", { count: members?.length ?? group.user_count })}</h3>
            <div className="relative w-56">
              <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={t("memberFilter")} className="h-8 pl-8" />
            </div>
            <Button size="sm" onClick={() => setAdding(true)} data-testid="access-group-add-users">
              <UserPlus className="size-3.5" />{t("addUsers")}
            </Button>
          </div>
          {isLoading ? (
            <div className="flex justify-center py-6"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
          ) : shown.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">{q ? t("noMatch") : t("noMembers")}</p>
          ) : (
            <div className="divide-y rounded-md border">
              {shown.map((m) => (
                <div key={m.user_id} className="flex items-center gap-3 px-3 py-2 text-sm" data-testid="access-group-member">
                  <span className="font-mono text-xs">{m.user_id}</span>
                  <span className="truncate text-muted-foreground">{m.display_name ?? m.email ?? ""}</span>
                  <span className="flex gap-1">
                    {!m.logged_in && <Badge variant="outline" className="text-amber-600">{t("notSignedIn")}</Badge>}
                    {m.personal_access === "all" && <Badge variant="outline" title={t("allAccessHint")}>{t("allAccess")}</Badge>}
                    {m.personal_key_count > 0 && <Badge variant="outline">{t("personalKeys", { count: m.personal_key_count })}</Badge>}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="ml-auto h-7 text-destructive hover:text-destructive"
                    onClick={() => removeUser(m.user_id)}
                    disabled={remove.isPending}
                  >
                    {t("remove")}
                  </Button>
                </div>
              ))}
            </div>
          )}
        </section>
      </CardContent>

      <GroupModelsDialog open={editing} onOpenChange={setEditing} models={models} group={group} />
      <AddUsersDialog open={adding} onOpenChange={setAdding} group={group.name} memberIds={memberIds} />
    </Card>
  );
}
