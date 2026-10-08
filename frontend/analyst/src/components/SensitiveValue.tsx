import { useEffect, useState } from "react";
import { maskSensitiveValue } from "../utils/masking";

export function SensitiveValue({ value, label }: { value?: string; label: string }) {
  const [revealed, setRevealed] = useState(false);
  useEffect(() => {
    if (!revealed) return;
    const timer = window.setTimeout(() => setRevealed(false), 30_000);
    return () => window.clearTimeout(timer);
  }, [revealed]);
  if (!value) return <span>Не определено</span>;
  return (
    <span className="sensitive-value">
      <code>{revealed ? value : maskSensitiveValue(value)}</code>
      <button type="button" aria-pressed={revealed} onClick={() => setRevealed((current) => !current)}>
        {revealed ? "Скрыть" : `Показать ${label} на 30 секунд`}
      </button>
    </span>
  );
}
