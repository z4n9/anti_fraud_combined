import { QualityIssueCard } from "../components/QualityIssueCard";
import { AnalystBrief } from "../components/AnalystBrief";
import { parseQualityIssues } from "../utils/dataQuality";

interface DataQualityPageProps {
  warnings: string[];
  targetPresent: boolean;
  targetValid: boolean;
}

export function DataQualityPage({ warnings, targetPresent, targetValid }: DataQualityPageProps) {
  const issues = parseQualityIssues(warnings);
  const missingIssues = issues.filter(({ kind }) => kind === "missing_features" || kind === "missing_rate");
  const unknownCount = issues
    .filter(({ kind }) => kind === "unknown_categories")
    .reduce((total, issue) => total + issue.items.length, 0);
  const extraCount = issues
    .filter(({ kind }) => kind === "extra_columns")
    .reduce((total, issue) => total + issue.items.length, 0);
  const attentionCount = issues.filter(({ level }) => level === "warning").length;

  const targetStatus = targetValid
    ? { value: "Доступен", detail: "GB_flag подходит для проверки модели", tone: "success" }
    : targetPresent
      ? { value: "Некорректен", detail: "Риск рассчитан, метрики недоступны", tone: "warning" }
      : { value: "Не передан", detail: "Это не мешает оценке риска", tone: "neutral" };

  return (
    <div className="section-page data-quality-page">
      <header className="section-page__intro">
        <p className="eyebrow">Контроль входных данных</p>
        <h1>Качество данных</h1>
        <p>Отклонения показывают, чем CSV отличается от обучающей выборки. Допустимое предупреждение не останавливает анализ.</p>
      </header>

      <AnalystBrief
        title="Что требует внимания"
        description="Предупреждения не всегда останавливают анализ, но могут влиять на надёжность отдельных оценок."
        steps={[
          { label: "Критичность", text: "Сначала изучите красные и жёлтые замечания" },
          { label: "Заполненность", text: "Проверьте пропуски и новые значения" },
          { label: "Источник", text: "Исправьте данные перед регулярным использованием" },
        ]}
      />

      <section className="data-quality-summary" aria-label="Сводка качества данных">
        <article className={`data-quality-stat data-quality-stat--${issues.length ? "warning" : "success"}`}>
          <span>Состояние файла</span>
          <strong>{issues.length ? "Есть отклонения" : "Структура принята"}</strong>
          <small>{issues.length ? `${attentionCount} требуют внимания` : "Модель не сообщила отклонений"}</small>
        </article>
        <article className={`data-quality-stat data-quality-stat--${missingIssues.length ? "warning" : "success"}`}>
          <span>Заполненность</span>
          <strong>{missingIssues.length ? `${missingIssues.length} сигнала` : "Без замечаний"}</strong>
          <small>{missingIssues.length ? "Проверьте пропуски и состав полей" : "Необычных пропусков не обнаружено"}</small>
        </article>
        <article className={`data-quality-stat data-quality-stat--${unknownCount ? "warning" : "success"}`}>
          <span>Новые категории</span>
          <strong>{unknownCount}</strong>
          <small>{unknownCount ? "Значения обработаны как неизвестные" : "Новых значений не обнаружено"}</small>
        </article>
        <article className={`data-quality-stat data-quality-stat--${extraCount ? "info" : "success"}`}>
          <span>Дополнительные поля</span>
          <strong>{extraCount}</strong>
          <small>{extraCount ? "Сохранены, но не используются моделью" : "Лишних полей не обнаружено"}</small>
        </article>
        <article className={`data-quality-stat data-quality-stat--${targetStatus.tone}`}>
          <span>Фактический результат</span>
          <strong>{targetStatus.value}</strong>
          <small>{targetStatus.detail}</small>
        </article>
      </section>

      {issues.length ? (
        <section className="panel data-quality-panel" aria-labelledby="data-quality-title">
          <div className="data-quality-panel__heading">
            <div><p className="eyebrow">Подробности</p><h2 id="data-quality-title">Обнаруженные отклонения</h2></div>
            <span>{issues.length}</span>
          </div>
          <p className="data-quality-panel__lead">Анализ завершён. Ниже объяснено влияние каждого отклонения и что стоит проверить в источнике данных.</p>
          <div className="quality-issue-list">
            {issues.map((issue) => <QualityIssueCard issue={issue} key={issue.id} />)}
          </div>
        </section>
      ) : (
        <section className="panel data-quality-empty" aria-labelledby="data-quality-title">
          <div aria-hidden="true">✓</div>
          <div>
            <p className="eyebrow">Проверка завершена</p>
            <h2 id="data-quality-title">Отклонений не обнаружено</h2>
            <p>Структура и заполненность загруженного CSV соответствуют требованиям текущей версии модели.</p>
          </div>
        </section>
      )}
    </div>
  );
}
