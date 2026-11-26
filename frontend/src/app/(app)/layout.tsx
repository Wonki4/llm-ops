import { AppSidebar } from "@/components/app-sidebar";
import { LocaleSync } from "@/components/locale-sync";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-dvh flex-col md:flex-row">
      <LocaleSync />
      <AppSidebar />
      <main className="min-h-0 min-w-0 flex-1 overflow-auto bg-muted/30 p-3 md:p-6">{children}</main>
    </div>
  );
}
