// Auto-generated from LiteLLM's provider->logo mapping.
// Maps a litellm_provider string to a logo served from /public/model-logos/.
// Source: litellm/ui/litellm-dashboard/src/components/provider_info_helpers.tsx

const PROVIDER_LOGO: Record<string, string> = {
  "a2a_agent": "a2a_agent.png",
  "ai21": "ai21.svg",
  "ai21_chat": "ai21.svg",
  "aiml": "aiml_api.svg",
  "aiohttp_openai": "openai_small.svg",
  "anthropic": "anthropic.svg",
  "anthropic_text": "anthropic.svg",
  "assemblyai": "assemblyai_small.png",
  "azure": "microsoft_azure.svg",
  "azure_ai": "microsoft_azure.svg",
  "azure_text": "microsoft_azure.svg",
  "baseten": "baseten.svg",
  "bedrock": "bedrock.svg",
  "bedrock_converse": "bedrock.svg",
  "bedrock_mantle": "bedrock.svg",
  "cerebras": "cerebras.svg",
  "cloudflare": "cloudflare.svg",
  "codestral": "mistral.svg",
  "cohere": "cohere.svg",
  "cohere_chat": "cohere.svg",
  "cometapi": "cometapi.svg",
  "cursor": "cursor.svg",
  "dashscope": "qwen.png",
  "databricks": "databricks.svg",
  "deepgram": "deepgram.png",
  "deepinfra": "deepinfra.png",
  "deepseek": "deepseek.svg",
  "elevenlabs": "elevenlabs.png",
  "fal_ai": "fal_ai.jpg",
  "featherless_ai": "featherless.svg",
  "fireworks_ai": "fireworks.svg",
  "friendliai": "friendli.svg",
  "gemini": "google.svg",
  "github_copilot": "github_copilot.svg",
  "groq": "groq.svg",
  "hosted_vllm": "vllm.png",
  "huggingface": "huggingface.svg",
  "hyperbolic": "hyperbolic.svg",
  "infinity": "infinity.png",
  "jina_ai": "jina.png",
  "lambda_ai": "lambda.svg",
  "lm_studio": "lmstudio.svg",
  "meta_llama": "meta_llama.svg",
  "minimax": "minimax.svg",
  "mistral": "mistral.svg",
  "moonshot": "moonshot.svg",
  "morph": "morph.svg",
  "nebius": "nebius.svg",
  "novita": "novita.svg",
  "nvidia_nim": "nvidia_nim.svg",
  "oci": "oracle.svg",
  "ollama": "ollama.svg",
  "ollama_chat": "ollama.svg",
  "oobabooga": "openai_small.svg",
  "openai": "openai_small.svg",
  "openai_like": "openai_small.svg",
  "openrouter": "openrouter.svg",
  "perplexity": "perplexity-ai.svg",
  "qwen": "qwen.png",
  "recraft": "recraft.svg",
  "replicate": "replicate.svg",
  "sagemaker": "bedrock.svg",
  "sagemaker_chat": "bedrock.svg",
  "sambanova": "sambanova.svg",
  "sap": "sap.png",
  "snowflake": "snowflake.svg",
  "text-completion-codestral": "mistral.svg",
  "text-completion-openai": "openai_small.svg",
  "together_ai": "togetherai.svg",
  "topaz": "topaz.svg",
  "triton": "nvidia_triton.png",
  "v0": "v0.svg",
  "vercel_ai_gateway": "vercel.svg",
  "vertex_ai": "google.svg",
  "vertex_ai_beta": "google.svg",
  "vllm": "vllm.png",
  "volcengine": "volcengine.png",
  "voyage": "voyage.webp",
  "watsonx": "watsonx.svg",
  "watsonx_text": "watsonx.svg",
  "xai": "xai.svg",
  "zai": "zai.svg",
  "xinference": "xinference.svg",
};

// Brand inference from the model name itself. This takes priority over the
// provider logo because the model name is the most specific brand signal: a
// model can be hosted on a multi-brand platform (Bedrock, Groq, OpenRouter,
// an openai-compatible endpoint, …) whose litellm_provider is the host, not
// the model maker — e.g. "bedrock/zai.glm-5" reports provider "bedrock_converse"
// but is clearly a GLM model. Ordered: the first substring match wins, so list
// more specific tokens first.
const NAME_LOGO: ReadonlyArray<readonly [string, string]> = [
  ["deepseek", "deepseek.svg"],
  ["kimi", "moonshot.svg"],
  ["moonshot", "moonshot.svg"],
  ["glm", "zai.svg"],
  ["qwen", "qwen.png"],
  ["qwq", "qwen.png"],
  ["claude", "anthropic.svg"],
  ["gemini", "google.svg"],
  ["gemma", "google.svg"],
  ["llama", "meta_llama.svg"],
  ["mixtral", "mistral.svg"],
  ["mistral", "mistral.svg"],
  ["codestral", "mistral.svg"],
  ["grok", "xai.svg"],
  ["command", "cohere.svg"],
  ["gpt", "openai_small.svg"],
];

function inferLogoFromName(modelName: string | null | undefined): string | null {
  if (!modelName) return null;
  const name = modelName.toLowerCase();
  for (const [token, file] of NAME_LOGO) {
    if (name.includes(token)) return file;
  }
  return null;
}

/**
 * Resolve a model's icon. Priority:
 *  1. explicit catalog icon_url
 *  2. brand inferred from the model name (so a GLM/Kimi/Qwen model hosted on
 *     Bedrock/Groq/OpenRouter still gets its maker's logo, not the host's)
 *  3. provider logo (host or first-party provider) as a fallback
 */
export function resolveModelIcon(
  iconUrl: string | null | undefined,
  litellmProvider: string | null | undefined,
  modelName?: string | null,
): string | null {
  if (iconUrl) return iconUrl;

  const byName = inferLogoFromName(modelName);
  if (byName) return `/model-logos/${byName}`;

  const provider = litellmProvider?.toLowerCase();
  const providerFile = provider ? PROVIDER_LOGO[provider] : undefined;
  if (providerFile) return `/model-logos/${providerFile}`;

  return null;
}
