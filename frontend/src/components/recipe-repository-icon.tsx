import { Boxes } from "lucide-react";
import { ModelIcon } from "@/components/model-icon";
import { hubAvatarOwners } from "@/lib/hub-avatar-owners";

// Hub namespaces identify the publisher. A fork can have the same model name as its upstream.
const ownerProviders = new Map([
  ["qwen", "qwen"],
  ["deepseek-ai", "deepseek"],
  ["meta-llama", "meta_llama"],
  ["mistralai", "mistral"],
  ["google", "gemini"],
  ["microsoft", "azure"],
  ["openai", "openai"],
  ["cohereforai", "cohere"],
  ["coherelabs", "cohere"],
  ["moonshotai", "moonshot"],
  ["zai-org", "zai"],
  ["nvidia", "nvidia_nim"],
]);

export function RecipeRepositoryIcon({ owner, size = 20 }: { owner?: string; size?: number }) {
  if (!owner) return <Boxes size={size} aria-hidden className="shrink-0 text-muted-foreground" />;
  const namespace = owner.toLowerCase();
  const avatar = hubAvatarOwners.has(namespace) ? `/model-logos/huggingface/${namespace}.webp` : undefined;
  return <ModelIcon key={namespace} iconUrl={avatar} provider={ownerProviders.get(namespace) ?? "huggingface"} size={size} />;
}
