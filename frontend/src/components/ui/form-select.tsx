"use client";

import { useRef, useState, type ComponentProps, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import {
  Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue,
} from "@/components/ui/select";

// Radix reserves the empty string for a cleared selection. Prefix every item
// value so even the clear option has a value, without reserving an application ID.
const OPTION_PREFIX = "option:";

type FormSelectProps = Omit<ComponentProps<typeof SelectTrigger>, "value" | "defaultValue" | "onChange" | "children" | "ref"> & {
  value: string | number;
  onValueChange: (value: string) => void;
  placeholder?: ReactNode;
  children: ReactNode;
  required?: boolean;
  name?: string;
};

export function FormSelect({
  value, onValueChange, placeholder = "—", children, disabled, required, name, form, className, ...props
}: FormSelectProps) {
  const trigger = useRef<HTMLButtonElement>(null);
  const [invalid, setInvalid] = useState(false);
  function change(next: string) {
    setInvalid(false);
    onValueChange(next.slice(OPTION_PREFIX.length));
  }
  return <><Select value={String(value) === "" ? "" : OPTION_PREFIX + value} onValueChange={change} disabled={disabled}>
    <SelectTrigger {...props} ref={trigger} aria-required={required || undefined}
      aria-invalid={(invalid && String(value) === "") || props["aria-invalid"]}
      className={cn("min-w-14 max-w-full", className)}>
      <SelectValue placeholder={placeholder} />
    </SelectTrigger>
    <SelectContent position="popper" className="min-w-48 w-[var(--radix-select-trigger-width)] max-w-[var(--radix-select-content-available-width)]">{children}</SelectContent>
  </Select>
    {/* Keep native form values/validation separate from Radix's nonempty clear option. */}
    {(required || name) && <input className="sr-only" tabIndex={-1} aria-hidden="true"
      value={String(value)} onChange={e => onValueChange(e.target.value)}
      required={required} disabled={disabled} name={name} form={form}
      onInvalid={e => { e.preventDefault(); setInvalid(true); trigger.current?.focus(); }} />}
  </>;
}

export function FormSelectItem({ value, children, className, ...props }: Omit<ComponentProps<typeof SelectItem>, "value"> & {
  value: string | number;
}) {
  return <SelectItem {...props} className={cn("[overflow-wrap:anywhere] [&>span:last-child]:min-w-0", className)}
    value={OPTION_PREFIX + value}>{children ?? "—"}</SelectItem>;
}

export function FormSelectGroup({ label, children }: { label: ReactNode; children: ReactNode }) {
  return <SelectGroup><SelectLabel>{label}</SelectLabel>{children}</SelectGroup>;
}
