export interface TransactionSignalPresentation {
  title: string;
  description: string;
  metrics: string[];
  impact: "Сильный фактор" | "Средний фактор" | "Дополнительный фактор";
}

type SignalPayload = Record<string, unknown>;

const descriptions: Record<string, string> = {
  large_amount: "Сумма заметно выше обычных операций этого клиента.",
  rapid_velocity: "Несколько переводов выполнены почти одновременно.",
  daily_velocity: "Число операций за сутки превышает привычную активность.",
  structuring: "Крупная сумма могла быть разделена на несколько небольших переводов.",
  new_recipient: "Средства отправлены новому получателю, которого не было в доступной истории.",
  night_activity: "Операция выполнена ночью и отличается от привычного поведения клиента.",
  rapid_cashout: "Недавно поступившие средства были быстро переведены дальше.",
  dormant_account: "До этой операции счёт долго не использовался или не встречался в доступной истории.",
  behavior_deviation: "Одновременно изменились сумма, устройство и география операции.",
  many_to_one: "На один счёт за короткое время поступили средства от нескольких отправителей.",
  short_cycle: "Средства прошли по цепочке счетов и вернулись к одному из участников.",
};

const money = (value: unknown) =>
  typeof value === "number"
    ? `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(value)} ₸`
    : null;

const number = (value: unknown, digits = 1) =>
  typeof value === "number"
    ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(value)
    : null;

function impact(payload: SignalPayload): TransactionSignalPresentation["impact"] {
  const strength = typeof payload.strength === "number"
    ? payload.strength
    : typeof payload.deviation_from_normal === "number"
      ? Math.min(1, Math.abs(payload.deviation_from_normal) / 8)
      : 0.35;
  if (strength >= 0.75) return "Сильный фактор";
  if (strength >= 0.4) return "Средний фактор";
  return "Дополнительный фактор";
}

function evidenceMetrics(evidence: SignalPayload): string[] {
  const metrics: string[] = [];
  const add = (label: string, value: string | null) => {
    if (value !== null) metrics.push(`${label}: ${value}`);
  };
  add("История", number(evidence.history_span_days) ? `${number(evidence.history_span_days)} дней` : null);
  add("Предыдущих операций", number(evidence.prior_operations, 0));
  add("Период без активности", number(evidence.inactive_days) ? `${number(evidence.inactive_days)} дней` : null);
  add("Сумма операции", money(evidence.amount));
  add("Обычная сумма", money(evidence.baseline_median));
  add("Операций за 5 минут", number(evidence.transactions_5m, 0));
  add("Операций за сутки", number(evidence.transactions_1d, 0));
  add("Операций за 30 минут", number(evidence.transactions_30m, 0));
  add("Общая сумма за 30 минут", money(evidence.total_30m));
  add("Недавнее поступление", money(evidence.inbound_amount));
  add("Отправителей за час", number(evidence.unique_senders_1h, 0));
  add("Поступило за час", money(evidence.incoming_amount_1h));
  add("Окно наблюдения", number(evidence.window_minutes, 0) ? `${number(evidence.window_minutes, 0)} мин.` : null);
  add("Период анализа связей", number(evidence.window_hours, 0) ? `${number(evidence.window_hours, 0)} ч.` : null);
  if (typeof evidence.amount_to_client_median === "number") {
    add("Выше обычной суммы", `${number(evidence.amount_to_client_median)} раза`);
  }
  if (typeof evidence.outbound_ratio === "number") {
    add("Доля выведенных средств", `${number(evidence.outbound_ratio * 100, 0)}%`);
  }
  if (typeof evidence.hour_utc === "number") {
    add("Время операции", `${String(evidence.hour_utc).padStart(2, "0")}:00 UTC`);
  }
  if (Array.isArray(evidence.path)) add("Участников в цепочке", String(evidence.path.length));
  if (evidence.new_device === true) metrics.push("Новое устройство");
  if (evidence.unusual_country === true) metrics.push("Новая страна");
  if (evidence.unusual_currency === true) metrics.push("Новая валюта");
  if (evidence.night === true) metrics.push("Ночное время");
  return metrics.slice(0, 4);
}

function parseInput(input: unknown): unknown[] {
  if (input === null || input === undefined || input === "") return [];
  if (Array.isArray(input)) return input.flatMap(parseInput);
  if (typeof input === "string") {
    try {
      return parseInput(JSON.parse(input));
    } catch {
      return [input];
    }
  }
  return [input];
}

function present(input: unknown): TransactionSignalPresentation {
  if (typeof input === "string") {
    return {
      title: input,
      description: "Система отметила это наблюдение как причину для дополнительной проверки.",
      metrics: [],
      impact: "Средний фактор",
    };
  }
  if (!input || typeof input !== "object") {
    return {
      title: "Нетипичное поведение операции",
      description: "Операция отличается от доступной истории и требует внимания аналитика.",
      metrics: [],
      impact: "Дополнительный фактор",
    };
  }
  const payload = input as SignalPayload;
  const code = typeof payload.code === "string" ? payload.code : "";
  const title = typeof payload.label === "string"
    ? payload.label
    : typeof payload.title === "string"
      ? payload.title
      : "Отклонение от обычного поведения";
  const evidence = payload.evidence && typeof payload.evidence === "object"
    ? payload.evidence as SignalPayload
    : {};
  const isMl = payload.explanation_type === "deviation_from_training_norm";
  const mlMetric = typeof payload.value === "number"
    ? [`Наблюдаемое значение: ${number(payload.value)}`]
    : [];
  return {
    title,
    description: descriptions[code]
      ?? (isMl
        ? "Показатель заметно отличается от значений, которые модель считает обычными."
        : "Этот признак усилил приоритет ручной проверки операции."),
    metrics: isMl ? mlMetric : evidenceMetrics(evidence),
    impact: impact(payload),
  };
}

export function getTransactionSignalPresentations(
  ...inputs: unknown[]
): TransactionSignalPresentation[] {
  return inputs.flatMap(parseInput).map(present).slice(0, 8);
}
