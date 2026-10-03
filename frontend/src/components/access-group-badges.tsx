import { Users } from "lucide-react";

import { Badge } from "@/components/ui/badge";

/** Inline list of LiteLLM access-group names. Renders nothing when empty. */
export function AccessGroupBadges({ groups, className }: { groups: string[]; className?: string }) {
  if (groups.length === 0) return null;
  return (
    <span className={"inline-flex flex-wrap items-center gap-1 " + (className ?? "")}>
      {groups.map((g) => (
        <Badge key={g} variant="outline" className="gap-1 px-1.5 py-0 font-mono text-[10px]" title={g}>
          <Users className="size-3" />
          {g}
        </Badge>
      ))}
    </span>
  );
}
