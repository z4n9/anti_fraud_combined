import type { AnalysisMetrics } from "../types/analysis";
import {
  asPercent,
  metricDefinitions,
  metricTone,
  metricValue,
  toneLabel,
  type MetricKey,
} from "../utils/modelQuality";
import { ConfusionMatrix } from "./ConfusionMatrix";
import { MetricCard } from "./MetricCard";

const simpleMetrics: Array<{
  key: MetricKey;
  title: string;
  conclusion: (value: string) => string;
}> = [
  { key: "roc_auc", title: "Модель правильно расставляет приоритеты", conclusion: (value) => `В ${value} пар рискованная запись получает более высокий приоритет.` },
  { key: "recall", title: "Большинство известных рисков найдено", conclusion: (value) => `Обнаружено ${value} записей, которые действительно относятся к мошенничеству.` },
  { key: "precision", title: "Не каждая тревога подтвердится", conclusion: (value) => `${value} сигналов тревоги подтверждаются фактической меткой.` },
];

export function MetricsPanel({ metrics }: { metrics: AnalysisMetrics }) {
  if (!metrics.available) {
    return (
      <section className="panel metrics-panel metrics-empty" aria-labelledby="metrics-title">
        <div className="metrics-empty__icon" aria-hidden="true">i</div>
        <div>
          <p className="eyebrow">Контроль качества</p>
          <h2 id="metrics-title">Метрики недоступны</h2>
          <p>
            В файле нет корректного GB_flag — столбца с фактическим результатом.
            Модель всё равно рассчитала риск и ранжировала записи, но проверить качество на этой выборке нельзя.
          </p>
          <p className="muted">Для расчёта метрик добавьте GB_flag со значениями 0 и 1 и запустите новый анализ.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="panel metrics-panel" aria-labelledby="metrics-title">
      <div className="section-heading">
        <p className="eyebrow">Контроль качества</p>
        <h2 id="metrics-title">Насколько хорошо работает модель</h2>
        <p className="section-description">
          Все показатели рассчитаны на фактических метках текущего CSV. Цвет показывает, где результат устойчив, а где нужен контроль.
        </p>
      </div>

      <div className="quality-simple simple-only" aria-label="Простое объяснение качества">
        {simpleMetrics.map((item) => {
          const definition = metricDefinitions.find(({ key }) => key === item.key)!;
          const value = metricValue(metrics, item.key);
          const tone = metricTone(value, definition);
          return (
            <article className={`quality-conclusion metric--${tone}`} key={item.key} data-source-metric={item.key}>
              <div className="quality-conclusion__topline">
                <span className="quality-conclusion__status">{toneLabel(tone)}</span>
                <strong>{asPercent(value)}</strong>
              </div>
              <h3>{item.title}</h3>
              <p>{item.conclusion(asPercent(value))}</p>
            </article>
          );
        })}
        <aside className="accuracy-note">
          <strong>Почему общая точность не главная?</strong>
          <p>
            Честных клиентов обычно намного больше. Поэтому Accuracy {asPercent(metricValue(metrics, "accuracy"))} может быть высокой,
            даже если модель пропускает риск. Для решения смотрите прежде всего на найденных мошенников и точность тревог.
          </p>
        </aside>
      </div>

      <div className="quality-expert expert-only" aria-label="Экспертные метрики качества">
        <div className="quality-expert__notice">
          <strong>Accuracy — справочная метрика.</strong>
          <span>На несбалансированной выборке она не отражает качество поиска редкого мошенничества сама по себе.</span>
        </div>
        <div className="metrics-grid">
          {metricDefinitions.map((definition) => (
            <MetricCard definition={definition} key={definition.key} value={metricValue(metrics, definition.key)} />
          ))}
        </div>
      </div>

      {metrics.confusion_matrix && <ConfusionMatrix matrix={metrics.confusion_matrix} />}
    </section>
  );
}
