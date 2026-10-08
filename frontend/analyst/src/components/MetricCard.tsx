import { asPercent, metricTone, toneLabel, type MetricDefinition } from "../utils/modelQuality";

export function MetricCard({ definition, value }: { definition: MetricDefinition; value: number }) {
  const percent = asPercent(value);
  const tone = metricTone(value, definition);
  const tooltipId = `metric-tooltip-${definition.key}`;

  return (
    <article className={`metric metric--${tone}`} data-metric={definition.key}>
      <div className="metric__heading">
        <span>{definition.label}</span>
        <span className="metric-tooltip" tabIndex={0} aria-label={`Что означает ${definition.code}`} aria-describedby={tooltipId}>
          ?
          <span id={tooltipId} role="tooltip">{definition.explanation(percent)}</span>
        </span>
      </div>
      <strong>{value.toFixed(3)}</strong>
      <div className="metric__footer">
        <small>{definition.code}</small>
        <small>{toneLabel(tone)} · {percent}</small>
      </div>
    </article>
  );
}
