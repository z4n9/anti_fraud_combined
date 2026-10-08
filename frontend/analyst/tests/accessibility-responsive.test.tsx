import axe from "axe-core";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConfirmationDialog } from "../src/components/ConfirmationDialog";
import { DashboardShell } from "../src/layout/DashboardShell";

afterEach(cleanup);

describe("F010 accessibility and responsive contract", () => {
  beforeEach(() => window.localStorage.clear());

  it("has no critical or serious axe violations in the dashboard shell", async () => {
    const { container } = render(
      <DashboardShell activeRoute="overview" phase="ready" onNavigate={vi.fn()}>
        <section aria-labelledby="test-heading">
          <h1 id="test-heading">Обзор риска</h1>
          <p>Сводка последнего анализа.</p>
        </section>
      </DashboardShell>,
    );

    const result = await axe.run(container, {
      rules: {
        // jsdom does not calculate rendered colours and contrast reliably.
        "color-contrast": { enabled: false },
      },
    });
    const blocking = result.violations.filter(({ impact }) =>
      impact === "critical" || impact === "serious",
    );
    expect(blocking).toEqual([]);
  });

  it("defines mobile, tablet, desktop and 200 percent safeguards", () => {
    const dashboardCss = readFileSync(
      resolve(process.cwd(), "src/styles/dashboard.css"),
      "utf8",
    );
    const tokensCss = readFileSync(
      resolve(process.cwd(), "src/styles/tokens.css"),
      "utf8",
    );

    expect(dashboardCss).toContain("@media (max-width: 1199px)");
    expect(dashboardCss).toContain("@media (max-width: 767px)");
    expect(dashboardCss).toContain("@media (max-width: 480px)");
    expect(dashboardCss).toContain("overflow-x: clip");
    expect(dashboardCss).toContain("table-layout: fixed");
    expect(dashboardCss).toContain("grid-template-columns: minmax(0, 1fr) auto");
    expect(dashboardCss).toContain("grid-template-columns: auto minmax(0, 1fr) auto");
    expect(dashboardCss).toContain("justify-self: end");
    expect(dashboardCss).toContain(".dashboard-root.scale-200");
    expect(dashboardCss).toContain("min-height: 44px");
    expect(tokensCss).toContain("font-size: calc(16px * var(--text-scale))");
    expect(tokensCss).toContain("outline: 3px solid #38bdf8");
  });

  it("starts keyboard navigation with a skip link and skips locked routes", async () => {
    const user = userEvent.setup();
    render(
      <DashboardShell activeRoute="new-analysis" phase="empty" onNavigate={vi.fn()}>
        <h1>Новый анализ</h1>
      </DashboardShell>,
    );

    await user.tab();
    expect(screen.getByRole("link", { name: "К основному содержимому" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("link", { name: "Risk Ledger — новый анализ" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("link", { name: "Новый анализ" })).toHaveFocus();
    await user.tab();
    if (screen.getByRole("button", { name: "Открыть меню" }) === document.activeElement) {
      await user.tab();
    }
    expect(screen.getByRole("button", { name: /Уведомления текущей сессии/ })).toHaveFocus();
  });

  it("applies the 200 percent text scale at the document root", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <DashboardShell activeRoute="overview" phase="ready" onNavigate={vi.fn()}>
        <h1>Обзор</h1>
      </DashboardShell>,
    );

    await user.click(screen.getByRole("button", { name: "Настройки отображения" }));
    await user.click(screen.getByRole("button", { name: "Масштаб текста 200%" }));

    expect(container.querySelector(".dashboard-root")).toHaveClass("scale-200");
    expect(document.documentElement.style.getPropertyValue("--text-scale")).toBe("2");
    expect(window.localStorage.getItem("risk-ledger-display-preferences:0")).toContain(
      '"textScale":200',
    );
  });

  it("traps focus inside confirmations and restores it after Escape", async () => {
    const user = userEvent.setup();

    function DialogHarness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>Удалить сессию</button>
          <ConfirmationDialog
            confirmLabel="Удалить"
            description="Локальные результаты будут удалены."
            open={open}
            title="Завершить сессию?"
            onCancel={() => setOpen(false)}
            onConfirm={() => setOpen(false)}
          />
        </>
      );
    }

    render(<DialogHarness />);
    const trigger = screen.getByRole("button", { name: "Удалить сессию" });
    await user.click(trigger);
    const cancel = screen.getByRole("button", { name: "Отмена" });
    const confirm = screen.getByRole("button", { name: "Удалить" });
    expect(cancel).toHaveFocus();

    await user.tab({ shift: true });
    expect(confirm).toHaveFocus();
    await user.tab();
    expect(cancel).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
