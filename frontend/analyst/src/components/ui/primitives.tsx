import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";
import { useId, useState } from "react";

type Tone = "neutral" | "success" | "warning" | "danger" | "info";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "danger";
}

export function Button({
  className = "",
  variant = "secondary",
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      className={`ui-button ui-button--${variant} ${className}`.trim()}
      type={type}
      {...props}
    />
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: Tone;
}) {
  return <span className={`ui-badge ui-badge--${tone}`}>{children}</span>;
}

export function Card({
  children,
  className = "",
  ...props
}: HTMLAttributes<HTMLElement>) {
  return (
    <section className={`ui-card ${className}`.trim()} {...props}>
      {children}
    </section>
  );
}

export function Tooltip({
  label,
  children = "?",
}: {
  label: string;
  children?: ReactNode;
}) {
  const tooltipId = useId();
  return (
    <span className="ui-tooltip" tabIndex={0} aria-describedby={tooltipId}>
      {children}
      <span id={tooltipId} role="tooltip">{label}</span>
    </span>
  );
}

export interface TabItem<T extends string> {
  id: T;
  label: string;
}

export function Tabs<T extends string>({
  active,
  items,
  label,
  onChange,
}: {
  active: T;
  items: readonly TabItem<T>[];
  label: string;
  onChange: (tab: T) => void;
}) {
  return (
    <div className="ui-tabs" role="tablist" aria-label={label}>
      {items.map((item) => (
        <button
          aria-selected={active === item.id}
          className={active === item.id ? "is-active" : ""}
          key={item.id}
          role="tab"
          type="button"
          onClick={() => onChange(item.id)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="ui-state ui-state--empty">
      <span className="ui-state__mark" aria-hidden="true">◇</span>
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function LoadingState({ label = "Загрузка данных" }: { label?: string }) {
  return (
    <div className="ui-state ui-state--loading" role="status">
      <span className="ui-spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function DemoTabs() {
  const [active, setActive] = useState<"summary" | "details">("summary");
  return (
    <Tabs
      active={active}
      items={[
        { id: "summary", label: "Сводка" },
        { id: "details", label: "Подробности" },
      ]}
      label="Демонстрационные вкладки"
      onChange={setActive}
    />
  );
}
