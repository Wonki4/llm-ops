"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { ChevronDown, ChevronRight, Loader2, Lock, Plus, Search, X } from "lucide-react";
import { toast } from "sonner";

import {
  useAddAccessGroupUsers,
  useAdminUsers,
  useCreateAccessGroup,
  usePreviewAccessGroupUsers,
  useUpdateAccessGroup,
} from "@/hooks/use-api";
import type { AccessGroup, AccessGroupCatalogModel, AccessGroupGrantResult } from "@/types";
import { normalizeUserId, splitPastedIds } from "@/lib/access-group-ids";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const errorText = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

// ─── models of a group (create / edit) ──────────────────────────────────────

/**
 * Pick a group's models. Without `group` it creates one (name + models). Models with
 * no DB deployment cannot be tagged by LiteLLM, and a member a config.yaml deployment
 * carries cannot leave: both show locked.
 */
export function GroupModelsDialog({
  open, onOpenChange, models, group, onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  models: AccessGroupCatalogModel[];
  group?: AccessGroup;
  onSaved?: (name: string) => void;
}) {
  const t = useTranslations("accessGroups");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{group ? t("editModelsTitle", { name: group.name }) : t("createTitle")}</DialogTitle>
          <DialogDescription>{t("modelsDialogHint")}</DialogDescription>
        </DialogHeader>
        <GroupModelsForm
          models={models}
          group={group}
          onSaved={(name) => { onOpenChange(false); onSaved?.(name); }}
          onCancel={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}

function GroupModelsForm({
  models, group, onSaved, onCancel,
}: {
  models: AccessGroupCatalogModel[];
  group?: AccessGroup;
  onSaved: (name: string) => void;
  onCancel: () => void;
}) {
  const t = useTranslations("accessGroups");
  const tc = useTranslations("common");
  const create = useCreateAccessGroup();
  const update = useUpdateAccessGroup();
  const lockedNames = useMemo(() => new Set(group?.models.filter((m) => m.locked).map((m) => m.model_name)), [group]);
  const [name, setName] = useState("");
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(() => new Set(group?.models.map((m) => m.model_name)));
  const pending = create.isPending || update.isPending;

  const shown = models.filter((m) => m.model_name.toLowerCase().includes(filter.trim().toLowerCase()));
  const toggle = (model: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(model)) next.delete(model);
      else next.add(model);
      return next;
    });

  function save() {
    const model_names = [...selected];
    const onError = (e: unknown) => toast.error(errorText(e, t("saveFailed")));
    if (group) {
      update.mutate({ group: group.name, model_names }, {
        onSuccess: () => { toast.success(t("modelsSaved")); onSaved(group.name); },
        onError,
      });
    } else {
      const trimmed = name.trim();
      create.mutate({ name: trimmed, model_names }, {
        onSuccess: () => { toast.success(t("created", { name: trimmed })); onSaved(trimmed); },
        onError,
      });
    }
  }

  return (
    <div className="space-y-4">
      {!group && (
        <div className="space-y-1.5">
          <Label htmlFor="access-group-name">{t("nameLabel")}</Label>
          <Input
            id="access-group-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("namePlaceholder")}
            className="font-mono"
            data-testid="access-group-name"
          />
          <p className="text-xs text-muted-foreground">{t("nameHint")}</p>
        </div>
      )}
      <div className="space-y-1.5">
        <Label>{t("modelsLabel", { count: selected.size })}</Label>
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={t("modelFilter")} className="h-8 pl-8" />
        </div>
        <div className="max-h-72 space-y-0.5 overflow-y-auto rounded-md border p-1">
          {shown.map((m) => {
            const locked = lockedNames.has(m.model_name);
            const disabled = locked || !m.editable;
            return (
              <label
                key={m.model_name}
                className={`flex items-center gap-2 rounded px-2 py-1 text-sm ${disabled ? "opacity-60" : "cursor-pointer hover:bg-accent"}`}
                title={locked ? t("lockedMemberHint") : !m.editable ? t("configModelHint") : undefined}
              >
                <input
                  type="checkbox"
                  checked={selected.has(m.model_name)}
                  disabled={disabled}
                  onChange={() => toggle(m.model_name)}
                  data-testid={`access-group-model-${m.model_name}`}
                />
                <span className="font-mono text-xs">{m.model_name}</span>
                {(locked || !m.editable) && <Lock className="size-3 text-muted-foreground" />}
                {m.access_groups.length > 0 && (
                  <span className="ml-auto truncate text-[11px] text-muted-foreground">{m.access_groups.join(", ")}</span>
                )}
              </label>
            );
          })}
          {shown.length === 0 && <p className="px-2 py-3 text-center text-xs text-muted-foreground">{t("noModels")}</p>}
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onCancel} disabled={pending}>{tc("cancel")}</Button>
        <Button
          onClick={save}
          disabled={pending || selected.size === 0 || (!group && !name.trim())}
          data-testid="access-group-save"
        >
          {pending && <Loader2 className="size-3.5 animate-spin" />}
          {group ? tc("save") : t("createButton")}
        </Button>
      </DialogFooter>
    </div>
  );
}

// ─── add users ──────────────────────────────────────────────────────────────

type Picked = { display_name: string | null; logged_in: boolean };

/**
 * Add users to a group: search and pick one by one; a roster can be pasted under
 * "명단 붙여넣기". Everyone picked collects below and is granted in one save. An id
 * nobody has signed in with yet can be picked too (granted ahead of the first login).
 */
export function AddUsersDialog({
  open, onOpenChange, group, memberIds,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  group: string;
  memberIds: Set<string>;
}) {
  const t = useTranslations("accessGroups");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("addUsersTitle", { name: group })}</DialogTitle>
          <DialogDescription>{t("addUsersHint")}</DialogDescription>
        </DialogHeader>
        <AddUsersForm group={group} memberIds={memberIds} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function AddUsersForm({ group, memberIds, onDone }: { group: string; memberIds: Set<string>; onDone: () => void }) {
  const t = useTranslations("accessGroups");
  const tc = useTranslations("common");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<Map<string, Picked>>(new Map());
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [pasteResults, setPasteResults] = useState<AccessGroupGrantResult[] | null>(null);
  const preview = usePreviewAccessGroupUsers();
  const add = useAddAccessGroupUsers();

  useEffect(() => {
    const timer = setTimeout(() => setSearch(searchInput.trim()), 250);
    return () => clearTimeout(timer);
  }, [searchInput]);
  const { data: found, isFetching } = useAdminUsers(1, 8, search, "");
  const results = found?.users ?? [];

  const pick = (userId: string, info: Picked) => setPicked((prev) => new Map(prev).set(userId, info));
  const unpick = (userId: string) =>
    setPicked((prev) => {
      const next = new Map(prev);
      next.delete(userId);
      return next;
    });

  // A typed id that matches nobody in the portal: pick it as "not signed in yet". Only once
  // the results are for what is in the box, so a stale search never offers the wrong id.
  const settled = search === searchInput.trim() && !isFetching;
  const typedId = settled ? normalizeUserId(search) : null;
  const typedIsNew =
    !!typedId && !results.some((u) => u.user_id === typedId) && !picked.has(typedId) && !memberIds.has(typedId);

  function loadPaste() {
    const ids = splitPastedIds(pasteText);
    if (ids.length === 0) return;
    preview.mutate({ group, user_ids: ids }, {
      onSuccess: (rows) => {
        setPasteResults(rows);
        setPicked((prev) => {
          const next = new Map(prev);
          for (const r of rows) {
            if (r.user_id && (r.status === "added" || r.status === "pregranted")) {
              next.set(r.user_id, { display_name: r.display_name, logged_in: r.logged_in });
            }
          }
          return next;
        });
        setPasteText("");
      },
      onError: (e) => toast.error(errorText(e, t("previewFailed"))),
    });
  }

  function save() {
    add.mutate({ group, user_ids: [...picked.keys()] }, {
      onSuccess: (rows) => {
        const count = (s: AccessGroupGrantResult["status"]) => rows.filter((r) => r.status === s).length;
        const failed = rows.filter((r) => r.status === "error").map((r) => r.user_id ?? r.input);
        toast.success(t("addedSummary", { added: count("added") + count("pregranted"), pregranted: count("pregranted") }));
        if (failed.length) toast.error(t("addFailedUsers", { users: failed.join(", ") }));
        onDone();
      },
      onError: (e) => toast.error(errorText(e, t("saveFailed"))),
    });
  }

  const byStatus = (s: AccessGroupGrantResult["status"]) => (pasteResults ?? []).filter((r) => r.status === s);

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder={t("userSearch")}
            className="pl-8"
            autoFocus
            data-testid="access-group-user-search"
          />
          {isFetching && <Loader2 className="absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 animate-spin text-muted-foreground" />}
        </div>
        <div className="max-h-56 space-y-0.5 overflow-y-auto rounded-md border p-1">
          {results.map((u) => {
            const member = memberIds.has(u.user_id);
            const chosen = picked.has(u.user_id);
            return (
              <div key={u.user_id} className="flex items-center gap-2 rounded px-2 py-1 text-sm" data-testid="access-group-user-result">
                <span className="font-mono text-xs">{u.user_id}</span>
                <span className="truncate text-muted-foreground">{u.display_name ?? u.email ?? ""}</span>
                <span className="ml-auto">
                  {member ? (
                    <span className="text-xs text-muted-foreground">{t("alreadyMember")}</span>
                  ) : chosen ? (
                    <span className="text-xs text-muted-foreground">{t("picked")}</span>
                  ) : (
                    <Button size="sm" variant="ghost" className="h-6 px-2" onClick={() => pick(u.user_id, { display_name: u.display_name, logged_in: true })}>
                      <Plus className="size-3" />{t("pick")}
                    </Button>
                  )}
                </span>
              </div>
            );
          })}
          {typedIsNew && typedId && (
            <div className="flex items-center gap-2 rounded px-2 py-1 text-sm" data-testid="access-group-user-typed">
              <span className="font-mono text-xs">{typedId}</span>
              <span className="truncate text-xs text-muted-foreground">{t("notSignedInHint")}</span>
              <Button size="sm" variant="ghost" className="ml-auto h-6 px-2" onClick={() => pick(typedId, { display_name: null, logged_in: false })}>
                <Plus className="size-3" />{t("pick")}
              </Button>
            </div>
          )}
          {results.length === 0 && !typedIsNew && (
            <p className="px-2 py-3 text-center text-xs text-muted-foreground">{t("noUsersFound")}</p>
          )}
        </div>
      </div>

      <div className="space-y-2">
        <button
          type="button"
          className="flex items-center gap-1 text-sm text-primary hover:underline"
          onClick={() => setPasteOpen((v) => !v)}
          data-testid="access-group-paste-toggle"
        >
          {pasteOpen ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          {t("pasteToggle")}
        </button>
        {pasteOpen && (
          <div className="space-y-2">
            <textarea
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
              placeholder={t("pastePlaceholder")}
              rows={4}
              className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs"
              data-testid="access-group-paste"
            />
            <Button size="sm" variant="outline" onClick={loadPaste} disabled={preview.isPending || !pasteText.trim()} data-testid="access-group-paste-load">
              {preview.isPending && <Loader2 className="size-3.5 animate-spin" />}
              {t("pasteLoad")}
            </Button>
          </div>
        )}
        {pasteResults && (
          <div className="space-y-0.5 text-xs" data-testid="access-group-paste-summary">
            {byStatus("added").length + byStatus("pregranted").length > 0 && (
              <p>✔ {t("pastePicked", { count: byStatus("added").length + byStatus("pregranted").length })}</p>
            )}
            {byStatus("already").length > 0 && (
              <p className="text-muted-foreground">· {t("pasteAlready", { users: byStatus("already").map((r) => r.user_id).join(", ") })}</p>
            )}
            {byStatus("invalid").length > 0 && (
              <p className="text-destructive">✕ {t("pasteInvalid", { inputs: byStatus("invalid").map((r) => r.input).join(", ") })}</p>
            )}
          </div>
        )}
      </div>

      <div className="space-y-1.5">
        <Label>{t("pickedTitle", { count: picked.size })}</Label>
        {picked.size === 0 ? (
          <p className="text-xs text-muted-foreground">{t("pickedEmpty")}</p>
        ) : (
          <div className="flex flex-wrap gap-1.5" data-testid="access-group-picked">
            {[...picked.entries()].map(([id, info]) => (
              <Badge key={id} variant="outline" className="gap-1 font-mono">
                {id}
                {info.display_name && <span className="font-sans text-muted-foreground">{info.display_name}</span>}
                {!info.logged_in && <span className="font-sans text-amber-600">{t("notSignedIn")}</span>}
                <button type="button" onClick={() => unpick(id)} aria-label={t("unpick", { id })}><X className="size-3" /></button>
              </Badge>
            ))}
          </div>
        )}
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onDone} disabled={add.isPending}>{tc("cancel")}</Button>
        <Button onClick={save} disabled={add.isPending || picked.size === 0} data-testid="access-group-add-save">
          {add.isPending && <Loader2 className="size-3.5 animate-spin" />}
          {t("addButton", { count: picked.size })}
        </Button>
      </DialogFooter>
    </div>
  );
}
