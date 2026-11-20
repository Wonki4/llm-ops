import { BenchmarkNavigation } from "@/components/benchmark-navigation";
export default function BenchmarkLayout({ children }: { children: React.ReactNode }) {
  return <div className="space-y-6"><BenchmarkNavigation />{children}</div>;
}
