"use client";

import { useState, useMemo } from "react";
import { Plus, Pencil, Trash2, Search, X, Loader2, Database, Settings } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  useRedisCatalog,
  useCatalogList,
  useUpdateCatalogList,
  useCreateRedisCatalogEntry,
  useUpdateRedisCatalogEntry,
  useDeleteRedisCatalogEntry,
  useModels,
} from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { RedisCatalogEntry } from "@/types";

interface FormState {
  display_name: string;
  model: string;
  apiBase: string;
  apiKey: string;
  options: string;
}

const INITIAL_FORM: FormState = {
  display_name: "",
  model: "",
  apiBase: "",
  apiKey: "",
  options: "{}",
};

export default function CatalogManagementPage() {
  const t = useTranslations("catalog");
  const tc = useTranslations("common");

  const { data: modelsData } = useModels();
  const modelNames = useMemo(() => {
    if (!modelsData) return [];
    return [...new Set(modelsData.map((m) => m.model_name))].sort();
  }, [modelsData]);

  const { data: catalogListData } = useCatalogList();
  const catalogs = catalogListData?.catalogs ?? [];
  const [activeCatalog, setActiveCatalog] = useState("");
  const currentCatalog = activeCatalog || catalogs[0] || "chat";

  const { data, isLoading, isError } = useRedisCatalog(currentCatalog);
  const createEntry = useCreateRedisCatalogEntry();
  const updateEntry = useUpdateRedisCatalogEntry();
  const deleteEntryMutation = useDeleteRedisCatalogEntry();
  const updateCatalogList = useUpdateCatalogList();

  const [searchQuery, setSearchQuery] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [editingName, setEditingName] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(INITIAL_FORM);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deletingName, setDeletingName] = useState<string | null>(null);
  const [catalogSettingsOpen, setCatalogSettingsOpen] = useState(false);
  const [catalogInput, setCatalogInput] = useState("");

  const entries = data?.entries ?? [];

  const filteredEntries = useMemo(() => {
    if (!searchQuery) return entries;
    const q = searchQuery.toLowerCase();
    return entries.filter(
      (e) =>
        e.display_name.toLowerCase().includes(q) ||
        e.model.toLowerCase().includes(q) ||
        e.apiBase.toLowerCase().includes(q),
    );
  }, [entries, searchQuery]);

  function openCreateDialog() {
    setEditingName(null);
    setForm(INITIAL_FORM);
    setFormOpen(true);
  }

  function openEditDialog(entry: RedisCatalogEntry) {
    setEditingName(entry.display_name);
    setForm({
      display_name: entry.display_name,
      model: entry.model || "",
      apiBase: entry.apiBase || "",
      apiKey: entry.apiKey || "",
      options: JSON.stringify(entry.options || {}, null, 2),
    });
    setFormOpen(true);
  }

  function openDeleteDialog(displayName: string) {
    setDeletingName(displayName);
    setDeleteOpen(true);
  }

  function handleSubmit() {
    if (!form.display_name.trim()) {
      toast.error(t("displayNameRequired"));
      return;
    }

    let parsedOptions: Record<string, unknown> = {};
    try {
      parsedOptions = JSON.parse(form.options || "{}");
    } catch {
      toast.error(t("invalidJsonOptions"));
      return;
    }

    const entryData = {
      model: form.model,
      apiBase: form.apiBase,
      apiKey: form.apiKey,
      options: parsedOptions,
    };

    if (editingName) {
      updateEntry.mutate(
        {
          catalog: currentCatalog,
          displayName: editingName,
          body: {
            entry: entryData,
            new_display_name: form.display_name !== editingName ? form.display_name : undefined,
          },
        },
        {
          onSuccess: () => { toast.success(t("updateSuccess")); setFormOpen(false); },
          onError: (err) => toast.error(err instanceof Error ? err.message : t("updateFailed")),
        },
      );
    } else {
      createEntry.mutate(
        { catalog: currentCatalog, body: { display_name: form.display_name, entry: entryData } },
        {
          onSuccess: () => { toast.success(t("createSuccess")); setFormOpen(false); },
          onError: (err) => toast.error(err instanceof Error ? err.message : t("createFailed")),
        },
      );
    }
  }

  function handleDelete() {
    if (!deletingName) return;
    deleteEntryMutation.mutate(
      { catalog: currentCatalog, displayName: deletingName },
      {
        onSuccess: () => { toast.success(t("deleteSuccess")); setDeleteOpen(false); setDeletingName(null); },
        onError: (err) => toast.error(err instanceof Error ? err.message : t("deleteFailed")),
      },
    );
  }

  function handleAddCatalog() {
    const name = catalogInput.trim();
    if (!name) return;
    if (catalogs.includes(name)) { toast.error(t("catalogAlreadyExists")); return; }
    updateCatalogList.mutate([...catalogs, name], {
      onSuccess: () => { toast.success(t("catalogAddSuccess", { name })); setCatalogInput(""); },
      onError: (err) => toast.error(err instanceof Error ? err.message : t("addFailed")),
    });
  }

  function handleRemoveCatalog(name: string) {
    if (catalogs.length <= 1) { toast.error(t("catalogMinRequired")); return; }
    updateCatalogList.mutate(catalogs.filter((c) => c !== name), {
      onSuccess: () => {
        toast.success(t("catalogRemoveSuccess", { name }));
        if (currentCatalog === name) setActiveCatalog("");
      },
    });
  }

  const isPending = createEntry.isPending || updateEntry.isPending;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{t("pageTitle")}</h1>
          <p className="text-muted-foreground mt-1">{t("pageDescription")}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={openCreateDialog}>
            <Plus className="size-4" />
            {t("registerButton")}
          </Button>
        </div>
      </div>

      {isError && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {t("loadError")}
        </div>
      )}

      {/* Catalog selector + search */}
      <div className="flex items-center gap-3 flex-wrap">
        <Select value={currentCatalog} onValueChange={setActiveCatalog}>
          <SelectTrigger className="w-[180px] h-9">
            <SelectValue placeholder={t("catalogSelectPlaceholder")} />
          </SelectTrigger>
          <SelectContent>
            {catalogs.map((c) => (
              <SelectItem key={c} value={c}>{c}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button variant="ghost" size="sm" className="h-9" onClick={() => setCatalogSettingsOpen(true)}>
          <Settings className="size-3.5" />
        </Button>
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
          <Input placeholder={t("searchPlaceholder")} value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} className="pl-8 h-9" />
        </div>
        {searchQuery && (
          <Button variant="ghost" size="sm" onClick={() => setSearchQuery("")}>
            <X className="size-3.5 mr-1" />
            {t("reset")}
          </Button>
        )}
        <span className="text-sm text-muted-foreground ml-auto">
          {t("entryCount", { filtered: filteredEntries.length, total: entries.length })}
        </span>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      ) : filteredEntries.length > 0 ? (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Display Name</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>API Base</TableHead>
                <TableHead>API Key</TableHead>
                <TableHead>Options</TableHead>
                <TableHead className="w-24">{t("colActions")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredEntries.map((entry) => (
                <TableRow key={entry.display_name}>
                  <TableCell className="font-medium">{entry.display_name}</TableCell>
                  <TableCell className="font-mono text-xs">{entry.model || "-"}</TableCell>
                  <TableCell className="font-mono text-xs max-w-[200px] truncate" title={entry.apiBase}>{entry.apiBase || "-"}</TableCell>
                  <TableCell className="text-sm">
                    {entry.apiKey ? <span className="text-green-600">{t("apiKeySet")}</span> : <span className="text-muted-foreground">-</span>}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground max-w-[150px] truncate">
                    {Object.keys(entry.options || {}).length > 0 ? JSON.stringify(entry.options) : "-"}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1">
                      <Button variant="ghost" size="icon-xs" onClick={() => openEditDialog(entry)}>
                        <Pencil className="size-3.5" />
                      </Button>
                      <Button variant="ghost" size="icon-xs" className="text-destructive hover:text-destructive" onClick={() => openDeleteDialog(entry.display_name)}>
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-16">
          <Database className="size-10 text-muted-foreground mb-3" />
          <p className="text-muted-foreground">{t("noEntries")}</p>
        </div>
      )}

      {/* Create / Edit Dialog */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingName ? t("editDialogTitle") : t("createDialogTitle")}</DialogTitle>
            <DialogDescription>{t("dialogCatalogLabel")}: <span className="font-semibold text-foreground">{currentCatalog}</span></DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Display Name *</Label>
              <Input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder={t("displayNamePlaceholder")} />
            </div>
            <div className="space-y-2">
              <Label>Model (LiteLLM Model Name)</Label>
              {modelNames.length > 0 ? (
                <Select value={form.model} onValueChange={(v) => setForm({ ...form, model: v })}>
                  <SelectTrigger>
                    <SelectValue placeholder={t("modelSelectPlaceholder")} />
                  </SelectTrigger>
                  <SelectContent>
                    {form.model && !modelNames.includes(form.model) && (
                      <SelectItem value={form.model}>{t("modelDirectInput", { model: form.model })}</SelectItem>
                    )}
                    {modelNames.map((name) => (
                      <SelectItem key={name} value={name}>{name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <Input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} placeholder={t("modelInputPlaceholder")} />
              )}
            </div>
            <div className="space-y-2">
              <Label>API Base</Label>
              <Input value={form.apiBase} onChange={(e) => setForm({ ...form, apiBase: e.target.value })} placeholder={t("apiBasePlaceholder")} />
            </div>
            <div className="space-y-2">
              <Label>API Key</Label>
              <Input type="password" value={form.apiKey} onChange={(e) => setForm({ ...form, apiKey: e.target.value })} placeholder="sk-..." />
            </div>
            <div className="space-y-2">
              <Label>Options (JSON)</Label>
              <textarea rows={4} value={form.options} onChange={(e) => setForm({ ...form, options: e.target.value })} placeholder='{"temperature": 0.7}'
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm font-mono shadow-xs placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)} disabled={isPending}>{tc("cancel")}</Button>
            <Button onClick={handleSubmit} disabled={isPending}>{isPending ? t("saving") : editingName ? t("editButton") : t("registerButton")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Dialog */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("deleteDialogTitle")}</DialogTitle>
            <DialogDescription><span className="font-semibold text-foreground">{deletingName}</span> {t("deleteDialogDescription")}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)} disabled={deleteEntryMutation.isPending}>{tc("cancel")}</Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleteEntryMutation.isPending}>{deleteEntryMutation.isPending ? t("deleting") : tc("delete")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Catalog Settings Dialog */}
      <Dialog open={catalogSettingsOpen} onOpenChange={setCatalogSettingsOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("catalogSettingsTitle")}</DialogTitle>
            <DialogDescription>{t("catalogSettingsDescription")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Input value={catalogInput} onChange={(e) => setCatalogInput(e.target.value)} placeholder={t("newCatalogPlaceholder")} className="h-9"
                onKeyDown={(e) => e.key === "Enter" && handleAddCatalog()} />
              <Button size="sm" onClick={handleAddCatalog} disabled={updateCatalogList.isPending}>
                <Plus className="size-3.5" />
                {t("addButton")}
              </Button>
            </div>
            <div className="space-y-2">
              {catalogs.map((c) => (
                <div key={c} className="flex items-center justify-between rounded-md border px-3 py-2">
                  <span className="text-sm font-mono">{c}</span>
                  <Button variant="ghost" size="icon-xs" className="text-destructive hover:text-destructive"
                    disabled={catalogs.length <= 1} onClick={() => handleRemoveCatalog(c)}>
                    <X className="size-3.5" />
                  </Button>
                </div>
              ))}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
