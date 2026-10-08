function percent(value: number, total: number) {
  return total > 0 ? `${((value / total) * 100).toFixed(1)}%` : "0.0%";
}

export function ConfusionMatrix({ matrix }: { matrix: number[][] }) {
  const tn = matrix[0]?.[0] ?? 0;
  const fp = matrix[0]?.[1] ?? 0;
  const fn = matrix[1]?.[0] ?? 0;
  const tp = matrix[1]?.[1] ?? 0;
  const honestTotal = tn + fp;
  const fraudTotal = fn + tp;

  return (
    <section className="matrix-section" aria-labelledby="matrix-title">
      <div className="matrix-section__heading">
        <div><p className="eyebrow">Факт и прогноз</p><h3 id="matrix-title">Матрица ошибок</h3></div>
        <p>Зелёные ячейки — верные решения, жёлтая — лишние проверки, красная — пропущенный риск.</p>
      </div>
      <div className="confusion" aria-label="Тепловая матрица ошибок модели">
        <span className="confusion__corner">Факт ↓ / Прогноз →</span>
        <span className="confusion__axis">Прогноз 0<small>Нет тревоги</small></span>
        <span className="confusion__axis">Прогноз 1<small>Сигнал тревоги</small></span>
        <span className="confusion__axis">Факт 0<small>Честные клиенты</small></span>
        <div className="matrix-cell matrix-cell--tn" aria-label={`Верно распознаны честные клиенты: ${tn}, ${percent(tn, honestTotal)}`}>
          <small>Верно: честные клиенты</small><strong>{tn}</strong><b>{percent(tn, honestTotal)} факта 0</b><span>True Negative</span>
        </div>
        <div className="matrix-cell matrix-cell--fp" aria-label={`Ложные тревоги: ${fp}, ${percent(fp, honestTotal)}`}>
          <small>Ложная тревога</small><strong>{fp}</strong><b>{percent(fp, honestTotal)} факта 0</b><span>False Positive</span>
        </div>
        <span className="confusion__axis">Факт 1<small>Мошенники</small></span>
        <div className="matrix-cell matrix-cell--fn" aria-label={`Пропущенный риск: ${fn}, ${percent(fn, fraudTotal)}`}>
          <small>Пропущенный риск</small><strong>{fn}</strong><b>{percent(fn, fraudTotal)} факта 1</b><span>False Negative</span>
        </div>
        <div className="matrix-cell matrix-cell--tp" aria-label={`Верно найден риск: ${tp}, ${percent(tp, fraudTotal)}`}>
          <small>Верно: найден риск</small><strong>{tp}</strong><b>{percent(tp, fraudTotal)} факта 1</b><span>True Positive</span>
        </div>
      </div>
    </section>
  );
}
