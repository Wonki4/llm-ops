/** A Kubernetes namespace name (RFC 1123 label) — same rule the backend enforces. */
const DNS_LABEL = /^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$/;

export function isValidNamespace(value: string): boolean {
  return DNS_LABEL.test(value);
}
