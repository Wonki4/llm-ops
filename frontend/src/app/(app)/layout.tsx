import { AppSidebar } from "@/components/app-sidebar";
import { LocaleSync } from "@/components/locale-sync";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen">
      <LocaleSync />
      <AppSidebar />
      <main className="flex-1 overflow-auto bg-muted/30 p-6">{children}</main>
    </div>
  );
}
