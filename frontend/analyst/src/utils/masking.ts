export function maskSensitiveValue(value: string): string {
  if (!value) return "—";
  const visible = value.length > 4 ? value.slice(-4) : value.slice(-1);
  return `${"•".repeat(Math.max(4, value.length - visible.length))}${visible}`;
}
