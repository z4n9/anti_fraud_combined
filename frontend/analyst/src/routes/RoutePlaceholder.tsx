import { EmptyState } from "../components/ui/primitives";
import type { DashboardRouteId } from "../types/dashboard";

const descriptions: Record<Exclude<DashboardRouteId, "new-analysis" | "overview">, string> = {
  "risk-records": "Таблица, графики, фильтры и подробная карточка будут подключены на этапе F005.",
  transactions: "Список подозрительных операций доступен после анализа транзакционного профиля.",
  relationships: "Граф связей доступен, когда модель распознала клиентов, счета или операции.",
  "model-quality": "Простой и экспертный режимы метрик будут подключены на этапе F007.",
  "data-quality": "Структурированная проверка качества будет подключена на этапе F008.",
};

export function RoutePlaceholder({
  route,
  onBack,
}: {
  route: Exclude<DashboardRouteId, "new-analysis" | "overview">;
  onBack: () => void;
}) {
  return (
    <EmptyState
      title="Раздел подготовлен"
      description={descriptions[route]}
      action={<button className="ui-button ui-button--secondary" type="button" onClick={onBack}>Вернуться к обзору</button>}
    />
  );
}
