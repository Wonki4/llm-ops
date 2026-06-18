import {
  ArrowRight,
  AudioLines,
  Binary,
  FileText,
  Image as ImageIcon,
  Type,
  type LucideIcon,
} from "lucide-react";

const MODALITY_ICONS: Record<string, { icon: LucideIcon; label: string }> = {
  text: { icon: Type, label: "Text" },
  image: { icon: ImageIcon, label: "Image" },
  audio: { icon: AudioLines, label: "Audio" },
  pdf: { icon: FileText, label: "PDF" },
  embedding: { icon: Binary, label: "Embedding" },
};

function get(obj: unknown, key: string): unknown {
  return obj && typeof obj === "object" ? (obj as Record<string, unknown>)[key] : undefined;
}

/** Derive input/output modality keys from litellm capability flags. */
export function modalitiesOf(info: unknown): { input: string[]; output: string[] } {
  const input = ["text"];
  if (get(info, "supports_vision") || get(info, "supports_embedding_image_input")) input.push("image");
  if (get(info, "supports_audio_input")) input.push("audio");
  if (get(info, "supports_pdf_input")) input.push("pdf");
  const output = get(info, "mode") === "embedding" ? ["embedding"] : ["text"];
  if (get(info, "supports_audio_output")) output.push("audio");
  return { input, output };
}

function ModalityIcons({ keys, size }: { keys: string[]; size: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      {keys.map((k) => {
        const m = MODALITY_ICONS[k];
        if (!m) return null;
        const Icon = m.icon;
        return <Icon key={k} className={size} aria-label={m.label}><title>{m.label}</title></Icon>;
      })}
    </span>
  );
}

/** Input modalities → output modalities, rendered as capability icons. */
export function ModalityValue({ info, size = "size-5" }: { info: unknown; size?: string }) {
  const { input, output } = modalitiesOf(info);
  return (
    <span className="inline-flex items-center gap-2">
      <ModalityIcons keys={input} size={size} />
      <ArrowRight className="size-4 text-muted-foreground" />
      <ModalityIcons keys={output} size={size} />
    </span>
  );
}
