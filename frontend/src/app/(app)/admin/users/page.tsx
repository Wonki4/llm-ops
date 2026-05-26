"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Search,
  Loader2,
  Users as UsersIcon,
  ChevronLeft,
  ChevronRight,
  ShieldCheck,
  X,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useLocaleTag } from "@/lib/locale";

import { useAdminUsers } from "@/hooks/use-api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const PAGE_SIZE_OPTIONS = [10, 30, 50, 100, 300] as const;
const DEFAULT_PAGE_SIZE = 50;

function formatBudget(value: number | null, unlimited: string): string {
  if (value == null) return unlimited;
  return `$${value.toFixed(2)}`;
}

function formatDate(dateStr: string | null, localeTag: string): string {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleDateString(localeTag, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

export default function AdminUsersPage() {
  const t = useTranslations("adminUsers");
  const localeTag = useLocaleTag();
  const tc = useTranslations("common");

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [role, setRole] = useState("");

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const { data, isLoading } = useAdminUsers(page, pageSize, search, role);
  const totalPages = data ? Math.ceil(data.total / pageSize) : 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">{t("pageTitle")}</h1>
        <p className="text-muted-foreground mt-1">
          {t("pageDescription")}
        </p>
      </div>

      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
          <Input
            placeholder={t("searchPlaceholder")}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="pl-8 h-9"
          />
        </div>
        <select
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          value={role}
          onChange={(e) => {
            setRole(e.target.value);
            setPage(1);
          }}
        >
          <option value="">{t("roleAll")}</option>
          <option value="super_user">{t("roleAdmin")}</option>
          <option value="user">{t("roleUser")}</option>
        </select>
        {(search || role) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSearchInput("");
              setSearch("");
              setRole("");
            }}
          >
            <X className="size-3.5 mr-1" />
            {t("reset")}
          </Button>
        )}
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      ) : !data || data.users.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-16">
          <UsersIcon className="size-10 text-muted-foreground mb-3" />
          <p className="text-muted-foreground">{t("noUsers")}</p>
        </div>
      ) : (
        <>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("colEmployeeId")}</TableHead>
                  <TableHead>{t("colName")}</TableHead>
                  <TableHead>{t("colEmail")}</TableHead>
                  <TableHead>{t("colRole")}</TableHead>
                  <TableHead className="text-right">{t("colKeys")}</TableHead>
                  <TableHead className="text-right">{t("colTeams")}</TableHead>
                  <TableHead className="text-right">{t("colUsage")}</TableHead>
                  <TableHead className="text-right">{t("colLimit")}</TableHead>
                  <TableHead>{t("colJoinedAt")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.users.map((u) => (
                  <TableRow
                    key={u.user_id}
                    className="cursor-pointer hover:bg-muted/50"
                    onClick={() => {
                      window.location.href = `/admin/users/${encodeURIComponent(u.user_id)}`;
                    }}
                  >
                    <TableCell className="font-mono text-sm">
                      <Link
                        href={`/admin/users/${encodeURIComponent(u.user_id)}`}
                        className="hover:underline"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {u.user_id}
                      </Link>
                    </TableCell>
                    <TableCell>{u.display_name || "-"}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {u.email || "-"}
                    </TableCell>
                    <TableCell>
                      {u.global_role === "super_user" ? (
                        <Badge className="bg-purple-100 text-purple-700 hover:bg-purple-100">
                          <ShieldCheck className="size-3 mr-1" />
                          {t("badgeAdmin")}
                        </Badge>
                      ) : (
                        <Badge variant="outline">{t("badgeUser")}</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {u.key_count}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {u.team_count}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      ${u.spend.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {formatBudget(u.max_budget, t("unlimited"))}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {formatDate(u.created_at, localeTag)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <p className="text-sm text-muted-foreground">
                {t("pagination", { total: data.total, start: (page - 1) * pageSize + 1, end: Math.min(page * pageSize, data.total) })}
              </p>
              <select
                className="h-8 rounded-md border border-input bg-background px-2 text-sm"
                value={pageSize}
                onChange={(e) => {
                  setPageSize(Number(e.target.value));
                  setPage(1);
                }}
              >
                {PAGE_SIZE_OPTIONS.map((size) => (
                  <option key={size} value={size}>
                    {t("perPage", { size })}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page === 1}
                onClick={() => setPage(page - 1)}
              >
                <ChevronLeft className="size-4" />
                {t("prev")}
              </Button>
              <span className="text-sm text-muted-foreground">
                {page} / {totalPages || 1}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => setPage(page + 1)}
              >
                {t("next")}
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
