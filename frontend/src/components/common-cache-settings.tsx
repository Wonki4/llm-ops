"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { Settings, X, Plus } from "lucide-react";
import { useCatalogList, useUpdateCatalogList } from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
export function CommonCacheSettings(){
  const t=useTranslations("settings");
  const { data: catalogListData } = useCatalogList();
  const updateCatalogList = useUpdateCatalogList();
  const catalogs: string[] = catalogListData?.catalogs ?? [];
  const [suffixInput, setSuffixInput] = useState("");
  function handleAddSuffix() {
    const name = suffixInput.trim();
    if (!name) return;
    if (catalogs.includes(name)) {
      toast.error(t("suffixAlreadyExists"));
      return;
    }
    updateCatalogList.mutate([...catalogs, name], {
      onSuccess: () => {
        toast.success(t("suffixAddSuccess", { suffix: name }));
        setSuffixInput("");
      },
      onError: (err) => toast.error(err instanceof Error ? err.message : t("addFailed")),
    });
  }

  function handleRemoveSuffix(name: string) {
    if (catalogs.length <= 1) {
      toast.error(t("suffixMinRequired"));
      return;
    }
    updateCatalogList.mutate(catalogs.filter((c) => c !== name), {
      onSuccess: () => toast.success(t("suffixRemoveSuccess", { suffix: name })),
      onError: (err) => toast.error(err instanceof Error ? err.message : t("removeFailed")),
    });
  }

  return (          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Settings className="size-4" />
                {t("cacheCatalogTitle")}
              </CardTitle>
              <CardDescription>
                {t("cacheCatalogDescription")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center gap-2">
                <Input
                  value={suffixInput}
                  onChange={(e) => setSuffixInput(e.target.value)}
                  placeholder={t("newSuffixPlaceholder")}
                  className="h-9"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleAddSuffix();
                    }
                  }}
                />
                <Button size="sm" onClick={handleAddSuffix} disabled={updateCatalogList.isPending}>
                  <Plus className="size-3.5" />
                  {t("addButton")}
                </Button>
              </div>
              <div className="space-y-2">
                {catalogs.length > 0 ? (
                  catalogs.map((c) => (
                    <div key={c} className="flex items-center justify-between rounded-md border px-3 py-2">
                      <span className="text-sm font-mono">{c}</span>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        className="text-destructive hover:text-destructive"
                        disabled={catalogs.length <= 1}
                        onClick={() => handleRemoveSuffix(c)}
                      >
                        <X className="size-3.5" />
                      </Button>
                    </div>
                  ))
                ) : (
                  <p className="text-sm text-muted-foreground">{t("noSuffixes")}</p>
                )}
              </div>
            </CardContent>
          </Card>);
}
