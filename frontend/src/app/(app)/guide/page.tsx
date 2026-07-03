"use client";

import Link from "next/link";
import { BarChart3, BookOpen, Boxes, TerminalSquare, Users } from "lucide-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { CodeBlock } from "@/components/code-block";

const CURL_EXAMPLE = `curl https://<gateway-host>/v1/chat/completions \\
  -H "Authorization: Bearer sk-your-key" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "<model-name>",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'`;

const PYTHON_EXAMPLE = `from openai import OpenAI

client = OpenAI(
    base_url="https://<gateway-host>/v1",
    api_key="sk-your-key",
)

resp = client.chat.completions.create(
    model="<model-name>",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp.choices[0].message.content)`;

export default function GuidePage() {
  const t = useTranslations("guide");

  const steps = [
    { id: "step-1", icon: Users, title: t("step1Title") },
    { id: "step-2", icon: TerminalSquare, title: t("step2Title") },
    { id: "step-3", icon: Boxes, title: t("step3Title") },
    { id: "step-4", icon: BarChart3, title: t("step4Title") },
  ];

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <div className="space-y-1">
        <h1 className="flex items-center gap-2 text-2xl font-bold">
          <BookOpen className="size-6" />
          {t("title")}
        </h1>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {/* Step anchor nav */}
      <nav className="flex flex-wrap gap-2">
        {steps.map((step, i) => (
          <a
            key={step.id}
            href={`#${step.id}`}
            className="rounded-full border px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-primary hover:text-foreground"
          >
            {t("stepLabel", { n: i + 1 })} · {step.title}
          </a>
        ))}
      </nav>

      {/* Step 1 — join a team & issue a key */}
      <section id="step-1" className="scroll-mt-6 space-y-4 rounded-lg border p-6">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <Users className="size-5 text-primary" />
          {t("stepLabel", { n: 1 })} · {t("step1Title")}
        </h2>
        <p className="text-sm text-muted-foreground">{t("step1Body")}</p>
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>{t("step1Item1")}</li>
          <li>{t("step1Item2")}</li>
        </ul>
        <p className="text-xs text-muted-foreground">{t("step1Note")}</p>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="outline" size="sm">
            <Link href="/teams/discover">{t("step1LinkDiscover")}</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link href="/keys/new">{t("step1LinkKeys")}</Link>
          </Button>
        </div>
      </section>

      {/* Step 2 — first API call */}
      <section id="step-2" className="scroll-mt-6 space-y-4 rounded-lg border p-6">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <TerminalSquare className="size-5 text-primary" />
          {t("stepLabel", { n: 2 })} · {t("step2Title")}
        </h2>
        <p className="text-sm text-muted-foreground">{t("step2Body")}</p>
        <div className="space-y-3">
          <div className="space-y-1">
            <div className="text-xs font-medium text-muted-foreground">{t("step2CurlLabel")}</div>
            <CodeBlock code={CURL_EXAMPLE} />
          </div>
          <div className="space-y-1">
            <div className="text-xs font-medium text-muted-foreground">{t("step2PythonLabel")}</div>
            <CodeBlock code={PYTHON_EXAMPLE} />
          </div>
        </div>
        <p className="text-xs text-muted-foreground">{t("step2Note")}</p>
      </section>

      {/* Step 3 — model catalog */}
      <section id="step-3" className="scroll-mt-6 space-y-4 rounded-lg border p-6">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <Boxes className="size-5 text-primary" />
          {t("stepLabel", { n: 3 })} · {t("step3Title")}
        </h2>
        <p className="text-sm text-muted-foreground">{t("step3Body")}</p>
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>{t("step3Item1")}</li>
          <li>{t("step3Item2")}</li>
        </ul>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="outline" size="sm">
            <Link href="/models/calendar">{t("step3LinkCalendar")}</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link href="/models/dashboard">{t("step3LinkDashboard")}</Link>
          </Button>
        </div>
      </section>

      {/* Step 4 — usage & cost */}
      <section id="step-4" className="scroll-mt-6 space-y-4 rounded-lg border p-6">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <BarChart3 className="size-5 text-primary" />
          {t("stepLabel", { n: 4 })} · {t("step4Title")}
        </h2>
        <p className="text-sm text-muted-foreground">{t("step4Body")}</p>
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>{t("step4Item1")}</li>
          <li>{t("step4Item2")}</li>
        </ul>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="outline" size="sm">
            <Link href="/teams">{t("step4LinkTeams")}</Link>
          </Button>
        </div>
      </section>
    </div>
  );
}
