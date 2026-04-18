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

function formatBudget(value: number | null): string {
  if (value == null) return "무제한";
  return `$${value.toFixed(2)}`;
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return "-";
  return new Date(dateStr).toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

export default function AdminUsersPage() {
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
        <h1 className="text-2xl font-bold">사용자 관리</h1>
        <p className="text-muted-foreground mt-1">
          포털에 등록된 전체 사용자를 사번 기준으로 조회합니다.
        </p>
      </div>

      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
          <Input
            placeholder="사번, 이메일, 이름 검색..."
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
          <option value="">전체 역할</option>
          <option value="super_user">관리자</option>
          <option value="user">일반 사용자</option>
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
            초기화
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
          <p className="text-muted-foreground">사용자가 없습니다.</p>
        </div>
      ) : (
        <>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>사번</TableHead>
                  <TableHead>이름</TableHead>
                  <TableHead>이메일</TableHead>
                  <TableHead>역할</TableHead>
                  <TableHead className="text-right">키</TableHead>
                  <TableHead className="text-right">팀</TableHead>
                  <TableHead className="text-right">사용량</TableHead>
                  <TableHead className="text-right">한도</TableHead>
                  <TableHead>가입일</TableHead>
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
                          관리자
                        </Badge>
                      ) : (
                        <Badge variant="outline">사용자</Badge>
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
                      {formatBudget(u.max_budget)}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {formatDate(u.created_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <p className="text-sm text-muted-foreground">
                총 {data.total}명 중 {(page - 1) * pageSize + 1}–
                {Math.min(page * pageSize, data.total)}
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
                    {size}개씩
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
                이전
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
                다음
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
