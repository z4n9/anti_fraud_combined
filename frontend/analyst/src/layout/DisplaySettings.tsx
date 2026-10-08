import type { DashboardPreferences } from "../types/dashboard";

interface DisplaySettingsProps {
  preferences: DashboardPreferences;
  onChange: (preferences: DashboardPreferences) => void;
}

export function DisplaySettings({ preferences, onChange }: DisplaySettingsProps) {
  const update = <K extends keyof DashboardPreferences>(
    key: K,
    value: DashboardPreferences[K],
  ) => onChange({ ...preferences, [key]: value });

  return (
    <details className="display-settings">
      <summary aria-label="Настройки отображения" role="button">Aa</summary>
      <div className="display-settings__panel">
        <fieldset>
          <legend>Плотность</legend>
          <div className="settings-options">
            <button
              aria-pressed={preferences.density === "comfortable"}
              type="button"
              onClick={() => update("density", "comfortable")}
            >Комфортная</button>
            <button
              aria-pressed={preferences.density === "compact"}
              type="button"
              onClick={() => update("density", "compact")}
            >Компактная</button>
          </div>
        </fieldset>
        <fieldset>
          <legend>Объяснения</legend>
          <div className="settings-options">
            <button
              aria-pressed={preferences.explanationMode === "simple"}
              type="button"
              onClick={() => update("explanationMode", "simple")}
            >Простые</button>
            <button
              aria-pressed={preferences.explanationMode === "expert"}
              type="button"
              onClick={() => update("explanationMode", "expert")}
            >Экспертные</button>
          </div>
        </fieldset>
        <fieldset>
          <legend>Текст</legend>
          <div className="settings-options">
            {([100, 112, 125, 150, 200] as const).map((scale) => (
              <button
                aria-label={`Масштаб текста ${scale}%`}
                aria-pressed={preferences.textScale === scale}
                key={scale}
                type="button"
                onClick={() => update("textScale", scale)}
              >{scale}%</button>
            ))}
          </div>
        </fieldset>
        <label className="contrast-toggle">
          <input
            checked={preferences.contrast === "high"}
            type="checkbox"
            onChange={(event) =>
              update("contrast", event.target.checked ? "high" : "standard")
            }
          />
          Повышенный контраст
        </label>
      </div>
    </details>
  );
}
