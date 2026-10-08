import type { AnalysisMetrics } from "../types/analysis";

export type MetricKey = "gini" | "ks" | "accuracy" | "precision" | "recall" | "roc_auc" | "pr_auc";
export type MetricTone = "good" | "attention" | "critical";

export interface MetricDefinition {
  key: MetricKey;
  label: string;
  code: string;
  good: number;
  attention: number;
  explanation: (percent: string) => string;
}

export const metricDefinitions: MetricDefinition[] = [
  { key: "gini", label: "Разделение клиентов", code: "Gini", good: 0.6, attention: 0.4, explanation: (value) => `Индекс разделения равен ${value}. Чем выше значение, тем увереннее модель различает обычные и рискованные записи.` },
  { key: "ks", label: "Различие групп", code: "KS", good: 0.4, attention: 0.25, explanation: (value) => `Максимальное различие между группами составляет ${value}. Высокое значение означает более чёткое разделение.` },
  { key: "accuracy", label: "Все верные решения", code: "Accuracy", good: 0.9, attention: 0.75, explanation: (value) => `Модель дала верный ответ для ${value} всех записей. При редком мошенничестве эта метрика может выглядеть лучше реального качества, поэтому не используется как главная.` },
  { key: "precision", label: "Точность тревог", code: "Precision", good: 0.7, attention: 0.4, explanation: (value) => `${value} отправленных на проверку записей действительно относятся к мошенничеству.` },
  { key: "recall", label: "Найденные мошенники", code: "Recall", good: 0.8, attention: 0.6, explanation: (value) => `Модель находит ${value} всех реальных мошенников в загруженном файле.` },
  { key: "roc_auc", label: "Качество ранжирования", code: "ROC-AUC", good: 0.8, attention: 0.7, explanation: (value) => `В ${value} сравнений мошенническая запись получает больший риск, чем обычная.` },
  { key: "pr_auc", label: "Качество редкого класса", code: "PR-AUC", good: 0.5, attention: 0.25, explanation: (value) => `Сводное качество поиска редкого класса равно ${value}. Показатель учитывает одновременно точность тревог и полноту обнаружения.` },
];

export function metricValue(metrics: AnalysisMetrics, key: MetricKey): number {
  const value = metrics[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function metricTone(value: number, definition: MetricDefinition): MetricTone {
  if (value >= definition.good) return "good";
  if (value >= definition.attention) return "attention";
  return "critical";
}

export function toneLabel(tone: MetricTone): string {
  if (tone === "good") return "Хороший уровень";
  if (tone === "attention") return "Требует внимания";
  return "Критический уровень";
}

export function asPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}
