import { getFeaturePresentation } from "./labels";

export type QualityIssueLevel = "info" | "warning" | "critical";
export type QualityIssueKind =
  | "extra_columns"
  | "unknown_categories"
  | "missing_features"
  | "missing_rate"
  | "target"
  | "incompatible"
  | "other";

export interface QualityIssue {
  id: string;
  kind: QualityIssueKind;
  level: QualityIssueLevel;
  title: string;
  description: string;
  items: string[];
  technicalCode?: string;
}

const splitValues = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);
const issueId = (source: string, index: number) => `${index}-${source.slice(0, 48)}`;

export function parseQualityIssue(source: string, index = 0): QualityIssue {
  const extra = /^Extra columns will be preserved but ignored by the model: (.+)$/.exec(source);
  if (extra) {
    return {
      id: issueId(source, index), kind: "extra_columns", level: "info",
      title: "Дополнительные столбцы",
      description: "Они сохранены в итоговом CSV, но не участвуют в расчёте риска. Результат анализа остаётся действительным.",
      items: splitValues(extra[1]), technicalCode: "EXTRA_COLUMNS",
    };
  }

  const unknown = /^Unknown categories in ([^:]+): (.+)$/.exec(source);
  if (unknown) {
    const feature = unknown[1].trim();
    return {
      id: issueId(source, index), kind: "unknown_categories", level: "warning",
      title: `Новые значения в поле «${getFeaturePresentation(feature).label}»`,
      description: "Таких категорий не было в обучающей выборке. Модель обработала их как неизвестные значения; рекомендуется проверить источник данных.",
      items: splitValues(unknown[2]), technicalCode: feature,
    };
  }

  const missing = /^Optional features will be imputed: (.+)$/.exec(source);
  if (missing) {
    return {
      id: issueId(source, index), kind: "missing_features", level: "warning",
      title: "Отсутствуют необязательные признаки",
      description: "Анализ продолжен: модель автоматически заполнила отсутствующие поля. Для более устойчивого результата проверьте формирование CSV.",
      items: splitValues(missing[1]), technicalCode: "OPTIONAL_FEATURES_IMPUTED",
    };
  }

  const drift = /^Missing rate drift for ([^:]+): ([\d.]+%) versus ([\d.]+%) in training\.$/.exec(source);
  if (drift) {
    const feature = drift[1].trim();
    return {
      id: issueId(source, index), kind: "missing_rate", level: "warning",
      title: `Изменилась заполненность поля «${getFeaturePresentation(feature).label}»`,
      description: `В текущем CSV отсутствует ${drift[2]} значений, в обучающей выборке — ${drift[3]}. Это может снизить устойчивость оценки риска.`,
      items: [], technicalCode: feature,
    };
  }

  const target = /^Target will be ignored for metrics: (.+)$/.exec(source);
  if (target) {
    return {
      id: issueId(source, index), kind: "target", level: "info",
      title: "Фактические метки не используются",
      description: "Риск рассчитан, но метрики качества модели недоступны: GB_flag должен содержать только значения 0 и 1.",
      items: [], technicalCode: "GB_flag",
    };
  }

  const critical = /^Missing critical features: (.+)$/.exec(source);
  if (critical) {
    return {
      id: issueId(source, index), kind: "incompatible", level: "critical",
      title: "Отсутствуют обязательные признаки",
      description: "Модель не может безопасно выполнить расчёт. Добавьте перечисленные столбцы и загрузите исправленный CSV.",
      items: splitValues(critical[1]), technicalCode: "MISSING_CRITICAL_FEATURES",
    };
  }

  const ratio = /^Missing feature ratio ([\d.]+%) exceeds the allowed ([\d.]+%)\.$/.exec(source);
  if (ratio) {
    return {
      id: issueId(source, index), kind: "incompatible", level: "critical",
      title: "Слишком много отсутствующих признаков",
      description: `В файле отсутствует ${ratio[1]} признаков при допустимом уровне ${ratio[2]}. Проверьте структуру CSV и повторите анализ.`,
      items: [], technicalCode: "MISSING_FEATURE_RATIO",
    };
  }

  return {
    id: issueId(source, index), kind: "other", level: "info",
    title: "Техническое уведомление о данных",
    description: "Файл обработан, но локальный сервис сообщил дополнительную диагностическую информацию. При повторении проверьте структуру CSV.",
    items: [], technicalCode: "DATA_DIAGNOSTIC",
  };
}

export function parseQualityIssues(sources: string[]): QualityIssue[] {
  return sources.map(parseQualityIssue);
}

export function issueLevelLabel(level: QualityIssueLevel): string {
  if (level === "critical") return "Критическая несовместимость";
  if (level === "warning") return "Требует внимания";
  return "Информация";
}

export function formatErrorDetail(detail: string): string {
  const issue = parseQualityIssue(detail);
  const list = issue.items.length ? `: ${issue.items.join(", ")}` : "";
  return `${issue.title}${list}. ${issue.description}`;
}
