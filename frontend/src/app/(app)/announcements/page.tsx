"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import { toast } from "sonner";
import {
  Loader2,
  Megaphone,
  Pencil,
  Plus,
  Trash2,
  EyeOff,
  Pin,
  Save,
  X,
  Star,
} from "lucide-react";

import {
  useAnnouncements,
  useCreateAnnouncement,
  useDeleteAnnouncement,
  useMe,
  useUpdateAnnouncement,
} from "@/hooks/use-api";
import type { Announcement } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

function formatDateTime(dateStr: string | null): string {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDateShort(dateStr: string | null): string {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

type PaneMode = { kind: "view" } | { kind: "create" } | { kind: "edit"; id: string };

export default function AnnouncementsPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center py-16"><Loader2 className="size-6 animate-spin text-muted-foreground" /></div>}>
      <AnnouncementsPageInner />
    </Suspense>
  );
}

function AnnouncementsPageInner() {
  const t = useTranslations("announcements");
  const { data: me } = useMe();
  const isAdmin = me?.role === "super_user";
  const { data: announcements, isLoading } = useAnnouncements(isAdmin);

  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedId = searchParams.get("id");

  const selectAnnouncement = (id: string | null, options?: { replace?: boolean }) => {
    const params = new URLSearchParams(searchParams.toString());
    if (id) params.set("id", id);
    else params.delete("id");
    const url = params.toString() ? `?${params.toString()}` : "";
    if (options?.replace) {
      router.replace(`/announcements${url}`, { scroll: false });
    } else {
      router.push(`/announcements${url}`, { scroll: false });
    }
  };

  const [mode, setMode] = useState<PaneMode>({ kind: "view" });

  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [isPublished, setIsPublished] = useState(true);
  const [isPinned, setIsPinned] = useState(false);
  const [isFeatured, setIsFeatured] = useState(false);
  const [confirmPublishOpen, setConfirmPublishOpen] = useState(false);

  const createMutation = useCreateAnnouncement();
  const updateMutation = useUpdateAnnouncement();
  const deleteMutation = useDeleteAnnouncement();

  const defaultAnnouncement = useMemo(
    () =>
      announcements?.find((a) => a.is_featured) ?? announcements?.[0] ?? null,
    [announcements],
  );

  const selected = useMemo(
    () =>
      announcements?.find((a) => a.id === selectedId) ??
      defaultAnnouncement,
    [announcements, selectedId, defaultAnnouncement],
  );

  useEffect(() => {
    if (!selectedId && defaultAnnouncement) {
      selectAnnouncement(defaultAnnouncement.id, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultAnnouncement, selectedId]);

  const startCreate = () => {
    setTitle("");
    setContent("");
    setIsPublished(true);
    setIsPinned(false);
    setIsFeatured(false);
    setMode({ kind: "create" });
  };

  const startEdit = (a: Announcement) => {
    setTitle(a.title);
    setContent(a.content);
    setIsPublished(a.is_published);
    setIsPinned(a.is_pinned);
    setIsFeatured(a.is_featured);
    setMode({ kind: "edit", id: a.id });
  };

  const cancelEdit = () => setMode({ kind: "view" });

  const handleSave = () => {
    if (!title.trim() || !content.trim()) {
      toast.error(t("toastRequireTitleContent"));
      return;
    }
    if (isFeatured && !isPublished) {
      setConfirmPublishOpen(true);
      return;
    }
    performSave(isPublished);
  };

  const performSave = (published: boolean) => {
    if (mode.kind === "create") {
      createMutation.mutate(
        {
          title,
          content,
          is_published: published,
          is_pinned: isPinned,
          is_featured: isFeatured,
        },
        {
          onSuccess: (created) => {
            toast.success(t("toastCreated"));
            selectAnnouncement(created.id);
            setMode({ kind: "view" });
          },
          onError: (err) =>
            toast.error(err instanceof Error ? err.message : t("errorCreate")),
        },
      );
    } else if (mode.kind === "edit") {
      updateMutation.mutate(
        {
          id: mode.id,
          body: {
            title,
            content,
            is_published: published,
            is_pinned: isPinned,
            is_featured: isFeatured,
          },
        },
        {
          onSuccess: () => {
            toast.success(t("toastUpdated"));
            setMode({ kind: "view" });
          },
          onError: (err) =>
            toast.error(err instanceof Error ? err.message : t("errorUpdate")),
        },
      );
    }
  };

  const handleDelete = (a: Announcement) => {
    if (!confirm(t("confirmDelete", { title: a.title }))) return;
    deleteMutation.mutate(a.id, {
      onSuccess: () => {
        toast.success(t("toastDeleted"));
        if (selectedId === a.id) selectAnnouncement(null, { replace: true });
      },
      onError: (err) =>
        toast.error(err instanceof Error ? err.message : t("errorDelete")),
    });
  };

  const handleSelectFromList = (id: string) => {
    if (mode.kind !== "view") {
      if (!confirm(t("confirmDiscardEdits"))) return;
    }
    selectAnnouncement(id);
    setMode({ kind: "view" });
  };

  const saving = createMutation.isPending || updateMutation.isPending;
  const isEditing = mode.kind === "edit" || mode.kind === "create";

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)] gap-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            {t("subtitle")}
          </p>
        </div>
        {isAdmin && !isEditing && (
          <Button onClick={startCreate}>
            <Plus className="size-4 mr-1" />
            {t("new")}
          </Button>
        )}
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center flex-1">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      ) : !announcements || announcements.length === 0 ? (
        mode.kind === "create" ? null : (
          <div className="flex flex-col items-center justify-center rounded-lg border border-dashed flex-1">
            <Megaphone className="size-10 text-muted-foreground mb-3" />
            <p className="text-muted-foreground">{t("empty")}</p>
          </div>
        )
      ) : null}

      {(announcements && announcements.length > 0) || mode.kind === "create" ? (
        <div
          className={cn(
            "grid gap-4 flex-1 min-h-0",
            isEditing ? "grid-cols-1" : "grid-cols-[320px_1fr]",
          )}
        >
          {!isEditing && (
          <aside className="rounded-lg border bg-background overflow-y-auto">
            <ul className="divide-y">
              {announcements?.map((a) => {
                const isSelected = selected?.id === a.id;
                return (
                  <li key={a.id}>
                    <button
                      type="button"
                      onClick={() => handleSelectFromList(a.id)}
                      className={cn(
                        "w-full text-left px-4 py-3 hover:bg-muted/50 transition-colors",
                        isSelected && "bg-muted",
                      )}
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        {a.is_featured && (
                          <Star className="size-3.5 text-yellow-500 shrink-0 fill-yellow-500" />
                        )}
                        {a.is_pinned && (
                          <Pin className="size-3.5 text-amber-500 shrink-0 fill-amber-500" />
                        )}
                        <h3 className="font-medium text-sm truncate flex-1">
                          {a.title}
                        </h3>
                        {!a.is_published && (
                          <Badge variant="outline" className="text-xs shrink-0">
                            <EyeOff className="size-2.5 mr-0.5" />
                            {t("draft")}
                          </Badge>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground mt-1">
                        {a.author_id} · {formatDateShort(a.created_at)}
                      </p>
                    </button>
                  </li>
                );
              })}
            </ul>
          </aside>
          )}

          <section className="rounded-lg border bg-background flex flex-col overflow-hidden">
            {isEditing ? (
              <EditorPane
                mode={mode.kind}
                title={title}
                content={content}
                isPublished={isPublished}
                isPinned={isPinned}
                isFeatured={isFeatured}
                saving={saving}
                onTitleChange={setTitle}
                onContentChange={setContent}
                onPublishedChange={setIsPublished}
                onPinnedChange={setIsPinned}
                onFeaturedChange={setIsFeatured}
                onSave={handleSave}
                onCancel={cancelEdit}
              />
            ) : selected ? (
              <ViewPane
                announcement={selected}
                isAdmin={isAdmin}
                onEdit={() => startEdit(selected)}
                onDelete={() => handleDelete(selected)}
                deleting={deleteMutation.isPending}
              />
            ) : null}
          </section>
        </div>
      ) : null}

      <Dialog open={confirmPublishOpen} onOpenChange={setConfirmPublishOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("featuredDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("featuredDialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">{t("cancel")}</Button>
            </DialogClose>
            <Button
              onClick={() => {
                setIsPublished(true);
                setConfirmPublishOpen(false);
                performSave(true);
              }}
              disabled={saving}
            >
              {saving && <Loader2 className="size-4 mr-1 animate-spin" />}
              {t("publishAndSave")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ViewPane({
  announcement,
  isAdmin,
  onEdit,
  onDelete,
  deleting,
}: {
  announcement: Announcement;
  isAdmin: boolean;
  onEdit: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  const t = useTranslations("announcements");
  return (
    <article className="p-6 space-y-4 overflow-y-auto">
      <header className="flex items-start justify-between gap-4 pb-4 border-b">
        <div className="min-w-0">
          <h2 className="text-2xl font-bold">{announcement.title}</h2>
          {(announcement.is_featured ||
            announcement.is_pinned ||
            !announcement.is_published) && (
            <div className="flex items-center gap-1.5 flex-wrap mt-2">
              {announcement.is_featured && (
                <Badge className="bg-yellow-100 text-yellow-700 hover:bg-yellow-100">
                  <Star className="size-3 mr-1 fill-yellow-500" />
                  {t("badgeFeatured")}
                </Badge>
              )}
              {announcement.is_pinned && (
                <Badge className="bg-amber-100 text-amber-700 hover:bg-amber-100">
                  <Pin className="size-3 mr-1 fill-amber-500" />
                  {t("badgePinned")}
                </Badge>
              )}
              {!announcement.is_published && (
                <Badge variant="outline">
                  <EyeOff className="size-3 mr-1" />
                  {t("badgeUnpublished")}
                </Badge>
              )}
            </div>
          )}
          <p className="text-xs text-muted-foreground mt-2">
            {announcement.author_id} · {formatDateTime(announcement.created_at)}
            {announcement.updated_at &&
              announcement.updated_at !== announcement.created_at && (
                <> · {t("updatedAt", { time: formatDateTime(announcement.updated_at) })}</>
              )}
          </p>
        </div>
        {isAdmin && (
          <div className="flex gap-1 shrink-0">
            <Button variant="ghost" size="sm" onClick={onEdit}>
              <Pencil className="size-3.5 mr-1" />
              {t("edit")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={onDelete}
              disabled={deleting}
            >
              <Trash2 className="size-3.5 mr-1" />
              {t("delete")}
            </Button>
          </div>
        )}
      </header>
      <div className="prose prose-sm max-w-none prose-headings:font-semibold prose-a:text-blue-600 prose-pre:bg-gray-50 prose-pre:border">
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>
          {announcement.content}
        </ReactMarkdown>
      </div>
    </article>
  );
}

function EditorPane({
  mode,
  title,
  content,
  isPublished,
  isPinned,
  isFeatured,
  saving,
  onTitleChange,
  onContentChange,
  onPublishedChange,
  onPinnedChange,
  onFeaturedChange,
  onSave,
  onCancel,
}: {
  mode: "create" | "edit";
  title: string;
  content: string;
  isPublished: boolean;
  isPinned: boolean;
  isFeatured: boolean;
  saving: boolean;
  onTitleChange: (v: string) => void;
  onContentChange: (v: string) => void;
  onPublishedChange: (v: boolean) => void;
  onPinnedChange: (v: boolean) => void;
  onFeaturedChange: (v: boolean) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const t = useTranslations("announcements");
  return (
    <div className="flex flex-col h-full overflow-hidden">
      <header className="flex items-center justify-between gap-3 px-6 py-3 border-b">
        <h2 className="font-semibold">
          {mode === "edit" ? t("editorTitleEdit") : t("editorTitleCreate")}
        </h2>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={onCancel} disabled={saving}>
            <X className="size-4 mr-1" />
            {t("cancel")}
          </Button>
          <Button size="sm" onClick={onSave} disabled={saving}>
            {saving ? (
              <Loader2 className="size-4 mr-1 animate-spin" />
            ) : (
              <Save className="size-4 mr-1" />
            )}
            {t("save")}
          </Button>
        </div>
      </header>

      <div className="px-6 py-3 border-b space-y-3">
        <Input
          value={title}
          onChange={(e) => onTitleChange(e.target.value)}
          placeholder={t("placeholderTitle")}
          className="text-lg font-semibold h-10"
        />
        <div className="flex items-center gap-6">
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input
              type="checkbox"
              checked={isPublished}
              onChange={(e) => onPublishedChange(e.target.checked)}
              className="size-4 rounded border-gray-300"
            />
            {t("togglePublished")}
          </label>
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input
              type="checkbox"
              checked={isPinned}
              onChange={(e) => onPinnedChange(e.target.checked)}
              className="size-4 rounded border-gray-300"
            />
            <Pin className="size-3.5 text-amber-500" />
            {t("togglePinned")}
          </label>
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input
              type="checkbox"
              checked={isFeatured}
              onChange={(e) => onFeaturedChange(e.target.checked)}
              className="size-4 rounded border-gray-300"
            />
            <Star className="size-3.5 text-yellow-500" />
            {t("toggleFeatured")}
            <span className="text-xs text-muted-foreground">{t("featuredHint")}</span>
          </label>
        </div>
      </div>

      <div className="grid grid-cols-2 flex-1 min-h-0 divide-x">
        <div className="flex flex-col min-h-0">
          <div className="px-4 py-2 border-b bg-muted/30 text-xs font-medium text-muted-foreground">
            Markdown
          </div>
          <textarea
            value={content}
            onChange={(e) => onContentChange(e.target.value)}
            placeholder={t("markdownPlaceholder")}
            className="flex-1 w-full px-4 py-3 text-sm font-mono resize-none focus:outline-none"
          />
        </div>
        <div className="flex flex-col min-h-0">
          <div className="px-4 py-2 border-b bg-muted/30 text-xs font-medium text-muted-foreground">
            {t("preview")}
          </div>
          <div className="flex-1 overflow-y-auto px-4 py-3">
            {content.trim() ? (
              <div className="prose prose-sm max-w-none">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeRaw]}
                >
                  {content}
                </ReactMarkdown>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                {t("previewEmpty")}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
