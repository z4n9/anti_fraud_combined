interface AnalystBriefProps {
  title: string;
  description: string;
  steps: Array<{ label: string; text: string }>;
}

export function AnalystBrief({ title, description, steps }: AnalystBriefProps) {
  return (
    <section className="analyst-brief" aria-label={title}>
      <div className="analyst-brief__intro">
        <span aria-hidden="true">◎</span>
        <div><strong>{title}</strong><p>{description}</p></div>
      </div>
      <ol>
        {steps.map((step, index) => (
          <li key={step.label}>
            <span>{index + 1}</span>
            <div><strong>{step.label}</strong><small>{step.text}</small></div>
          </li>
        ))}
      </ol>
    </section>
  );
}
