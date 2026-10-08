import type { ExplanationFactor } from "../types/analysis";
import { formatFeatureValue, getFeaturePresentation } from "../utils/labels";

const contribution = new Intl.NumberFormat("ru-RU", {
  maximumFractionDigits: 4,
  signDisplay: "always",
});

export function FactorCard({ factor }: { factor: ExplanationFactor }) {
  const feature = getFeaturePresentation(factor.feature);
  const increases = factor.direction === "increases_risk";
  const displayLabel = feature.label === factor.feature ? "Технический признак" : feature.label;
  return (
    <li
      className={`factor-card ${increases ? "factor-card--up" : "factor-card--down"}`}
      title={feature.description}
    >
      <span className="factor-card__direction" aria-hidden="true">{increases ? "↑" : "↓"}</span>
      <div className="factor-card__identity">
        <span className="factor-list__label">{displayLabel}</span>
      </div>
      <strong className="factor-card__value">{formatFeatureValue(factor.value, factor.feature)}</strong>
      <p className="factor-card__description">{feature.description}</p>
      <small className="factor-card__risk-direction">{increases ? "Повышает риск" : "Снижает риск"}</small>
      <span className="factor-card__contribution">
        Влияние на риск <strong>{contribution.format(factor.contribution)}</strong>
      </span>
    </li>
  );
}
