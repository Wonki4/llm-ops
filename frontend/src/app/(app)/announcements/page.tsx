"use client";

import { useEffect, useMemo, useState } from "react";
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
  const { data: me } = useMe();
  const isAdmin = me?.role === "super_user";
  const { data: announcements, isLoading } = useAnnouncements(isAdmin);

  const [selectedId, setSelectedId] = useState<string | null>(null);
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
      setSelectedId(defaultAnnouncement.id);
    }
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
      toast.error("제목과 내용을 입력해주세요.");
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
            toast.success("공지사항이 등록되었습니다.");
            setSelectedId(created.id);
            setMode({ kind: "view" });
          },
          onError: (err) =>
            toast.error(err instanceof Error ? err.message : "등록 실패"),
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
            toast.success("공지사항이 수정되었습니다.");
            setMode({ kind: "view" });
          },
          onError: (err) =>
            toast.error(err instanceof Error ? err.message : "수정 실패"),
        },
      );
    }
  };

  const handleDelete = (a: Announcement) => {
    if (!confirm(`"${a.title}" 공지사항을 삭제하시겠습니까?`)) return;
    deleteMutation.mutate(a.id, {
      onSuccess: () => {
        toast.success("삭제되었습니다.");
        if (selectedId === a.id) setSelectedId(null);
      },
      onError: (err) =>
        toast.error(err instanceof Error ? err.message : "삭제 실패"),
    });
  };

  const handleSelectFromList = (id: string) => {
    if (mode.kind !== "view") {
      if (!confirm("편집 중인 내용이 있습니다. 무시하고 이동할까요?")) return;
    }
    setSelectedId(id);
    setMode({ kind: "view" });
  };

  const saving = createMutation.isPending || updateMutation.isPending;
  const isEditing = mode.kind === "edit" || mode.kind === "create";

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)] gap-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">공지사항</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            포털 업데이트, 정책 변경 등 중요한 소식을 확인하세요.
          </p>
        </div>
        {isAdmin && !isEditing && (
          <Button onClick={startCreate}>
            <Plus className="size-4 mr-1" />
            작성
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
            <p className="text-muted-foreground">등록된 공지사항이 없습니다.</p>
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
                            draft
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
            <DialogTitle>대표 공지는 게시 상태여야 합니다</DialogTitle>
            <DialogDescription>
              미게시 상태의 공지는 대표 공지로 설정할 수 없습니다. 게시 상태로
              전환하고 저장할까요?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">취소</Button>
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
              게시하고 저장
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
                  대표 공지
                </Badge>
              )}
              {announcement.is_pinned && (
                <Badge className="bg-amber-100 text-amber-700 hover:bg-amber-100">
                  <Pin className="size-3 mr-1 fill-amber-500" />
                  상단 고정
                </Badge>
              )}
              {!announcement.is_published && (
                <Badge variant="outline">
                  <EyeOff className="size-3 mr-1" />
                  미게시
                </Badge>
              )}
            </div>
          )}
          <p className="text-xs text-muted-foreground mt-2">
            {announcement.author_id} · {formatDateTime(announcement.created_at)}
            {announcement.updated_at &&
              announcement.updated_at !== announcement.created_at && (
                <> · 수정 {formatDateTime(announcement.updated_at)}</>
              )}
          </p>
        </div>
        {isAdmin && (
          <div className="flex gap-1 shrink-0">
            <Button variant="ghost" size="sm" onClick={onEdit}>
              <Pencil className="size-3.5 mr-1" />
              수정
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={onDelete}
              disabled={deleting}
            >
              <Trash2 className="size-3.5 mr-1" />
              삭제
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
  return (
    <div className="flex flex-col h-full overflow-hidden">
      <header className="flex items-center justify-between gap-3 px-6 py-3 border-b">
        <h2 className="font-semibold">
          {mode === "edit" ? "공지사항 수정" : "새 공지사항"}
        </h2>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={onCancel} disabled={saving}>
            <X className="size-4 mr-1" />
            취소
          </Button>
          <Button size="sm" onClick={onSave} disabled={saving}>
            {saving ? (
              <Loader2 className="size-4 mr-1 animate-spin" />
            ) : (
              <Save className="size-4 mr-1" />
            )}
            저장
          </Button>
        </div>
      </header>

      <div className="px-6 py-3 border-b space-y-3">
        <Input
          value={title}
          onChange={(e) => onTitleChange(e.target.value)}
          placeholder="제목"
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
            게시
          </label>
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input
              type="checkbox"
              checked={isPinned}
              onChange={(e) => onPinnedChange(e.target.checked)}
              className="size-4 rounded border-gray-300"
            />
            <Pin className="size-3.5 text-amber-500" />
            상단 고정
          </label>
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input
              type="checkbox"
              checked={isFeatured}
              onChange={(e) => onFeaturedChange(e.target.checked)}
              className="size-4 rounded border-gray-300"
            />
            <Star className="size-3.5 text-yellow-500" />
            대표 공지
            <span className="text-xs text-muted-foreground">(최초 진입시 선택됨, 1개만)</span>
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
            placeholder={"# 제목\n\n내용을 입력하세요...\n\n| 헤더 | 값 |\n|---|---|\n| A | 1 |"}
            className="flex-1 w-full px-4 py-3 text-sm font-mono resize-none focus:outline-none"
          />
        </div>
        <div className="flex flex-col min-h-0">
          <div className="px-4 py-2 border-b bg-muted/30 text-xs font-medium text-muted-foreground">
            미리보기
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
                왼쪽에 내용을 입력하면 여기에 미리보기가 표시됩니다.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
