import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { Badge, EmptyState, Tabs, Tooltip } from "../src/components/ui/primitives";

afterEach(cleanup);

describe("dashboard UI primitives", () => {
  it("renders semantic statuses and accessible helper content", () => {
    render(
      <>
        <Badge tone="danger">Критический риск</Badge>
        <Tooltip label="Пояснение метрики" />
        <EmptyState title="Нет данных" description="Измените фильтры." />
      </>,
    );
    expect(screen.getByText("Критический риск")).toHaveClass("ui-badge--danger");
    expect(screen.getByRole("tooltip")).toHaveTextContent("Пояснение метрики");
    expect(screen.getByRole("heading", { name: "Нет данных" })).toBeInTheDocument();
  });

  it("changes the active tab", async () => {
    const user = userEvent.setup();
    let active: "summary" | "factors" = "summary";
    const { rerender } = render(
      <Tabs
        active={active}
        items={[
          { id: "summary", label: "Сводка" },
          { id: "factors", label: "Факторы" },
        ]}
        label="Карточка записи"
        onChange={(tab) => { active = tab; }}
      />,
    );
    await user.click(screen.getByRole("tab", { name: "Факторы" }));
    rerender(
      <Tabs
        active={active}
        items={[
          { id: "summary", label: "Сводка" },
          { id: "factors", label: "Факторы" },
        ]}
        label="Карточка записи"
        onChange={(tab) => { active = tab; }}
      />,
    );
    expect(screen.getByRole("tab", { name: "Факторы" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});
