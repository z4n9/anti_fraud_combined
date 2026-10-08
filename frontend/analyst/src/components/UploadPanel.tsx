import { useRef, useState } from "react";

const MAX_FILE_BYTES = 500 * 1024 * 1024;
const SUPPORTED_EXTENSIONS = [
  ".csv", ".json", ".jsonl", ".ndjson", ".sql", ".sqlite", ".sqlite3", ".db", ".bson",
];

function fileExtension(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot >= 0 ? filename.slice(dot).toLowerCase() : "";
}

function formatFileSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
  return `${Math.max(0.1, bytes / 1024).toFixed(1)} КБ`;
}

interface UploadPanelProps {
  disabled?: boolean;
  onUpload: (file: File) => void;
}

export function UploadPanel({ disabled = false, onUpload }: UploadPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);

  const selectFile = (candidate: File | null) => {
    setValidationError(null);
    if (!candidate) {
      setFile(null);
      return;
    }
    if (!SUPPORTED_EXTENSIONS.includes(fileExtension(candidate.name))) {
      setFile(null);
      setValidationError("Выберите CSV, JSON, SQL, SQLite или BSON-файл.");
      return;
    }
    if (candidate.size === 0) {
      setFile(null);
      setValidationError("Файл пуст. Добавьте источник с данными для анализа.");
      return;
    }
    if (candidate.size > MAX_FILE_BYTES) {
      setFile(null);
      setValidationError("Размер файла превышает допустимые 500 МБ.");
      return;
    }
    setFile(candidate);
  };

  return (
    <section className="upload-panel" aria-labelledby="upload-title">
      <div>
        <p className="eyebrow">Новый анализ</p>
        <h2 id="upload-title">Передайте выборку модели</h2>
        <p className="muted">
          CSV, JSON, SQL, SQLite или BSON до 500 МБ. Структура определяется
          автоматически, данные остаются на этом компьютере.
        </p>
      </div>
      <div
        className={`drop-zone${file ? " drop-zone--selected" : ""}`}
        aria-describedby={validationError ? "upload-validation-error" : undefined}
        onClick={() => !disabled && inputRef.current?.click()}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          const dropped = event.dataTransfer.files[0];
          if (!disabled) selectFile(dropped ?? null);
        }}
        onKeyDown={(event) => {
          if (!disabled && (event.key === "Enter" || event.key === " ")) {
            inputRef.current?.click();
          }
        }}
        role="button"
        tabIndex={0}
      >
        <input
          ref={inputRef}
          aria-label="Выберите файл с данными"
          accept={SUPPORTED_EXTENSIONS.join(",")}
          disabled={disabled}
          hidden
          type="file"
          onChange={(event) => selectFile(event.target.files?.[0] ?? null)}
        />
        <span className="drop-zone__mark" aria-hidden="true">
          {file ? fileExtension(file.name).slice(1).toUpperCase() : "DATA"}
        </span>
        <strong>{file ? file.name : "Выберите файл с данными"}</strong>
        <span>{file ? formatFileSize(file.size) : "или перетащите его сюда"}</span>
        {file && (
          <button
            className="drop-zone__replace"
            disabled={disabled}
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              inputRef.current?.click();
            }}
          >Заменить файл</button>
        )}
      </div>
      {validationError && (
        <p className="upload-validation" id="upload-validation-error" role="alert">
          {validationError}
        </p>
      )}
      <button
        className="button button--primary"
        disabled={!file || disabled}
        type="button"
        onClick={() => file && onUpload(file)}
      >
        {disabled ? "Проверяем источник" : "Проверить источник"}
      </button>
    </section>
  );
}
