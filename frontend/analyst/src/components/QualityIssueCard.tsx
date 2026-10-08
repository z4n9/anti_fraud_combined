import type { QualityIssue } from "../utils/dataQuality";
import { issueLevelLabel } from "../utils/dataQuality";

export function QualityIssueCard({ issue }: { issue: QualityIssue }) {
  return (
    <article className={`quality-issue quality-issue--${issue.level}`}>
      <div className="quality-issue__mark" aria-hidden="true">
        {issue.level === "critical" ? "×" : issue.level === "warning" ? "!" : "i"}
      </div>
      <div className="quality-issue__body">
        <span className="quality-issue__level">{issueLevelLabel(issue.level)}</span>
        <h3>{issue.title}</h3>
        <p>{issue.description}</p>
        {(issue.technicalCode || issue.items.length > 0) && (
          <details>
            <summary>Показать коды и значения</summary>
            {issue.technicalCode && <div className="quality-issue__code"><span>Код</span><code>{issue.technicalCode}</code></div>}
            {issue.items.length > 0 && (
              <ul aria-label="Технические значения">
                {issue.items.map((item) => <li key={item}><code>{item}</code></li>)}
              </ul>
            )}
          </details>
        )}
      </div>
    </article>
  );
}
