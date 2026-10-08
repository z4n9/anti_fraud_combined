# Уборка конкурсного выпуска — 8 октября 2026

Удалены два устаревших файла `frontend/analyst/Dockerfile` и `frontend/analyst/nginx.conf`. Они описывали отдельный nginx/frontend с отсутствующим в целевой конфигурации backend:8000. Внешние ссылки отсутствовали; оба интерфейса теперь собирает корневой Dockerfile. UI и аналитические возможности сохранены.

После завершения всех тестов `clean.ps1 -Apply` удалил **23 генерируемых каталога**:

- `.test-tmp` и `.qa-security-tmp` с историческими тестовыми БД и временными smoke-скриптами.
- `backend/.pytest_cache` и шесть `backend/pytest-cache-files-*`.
- Четырнадцать `backend/**/__pycache__`.

Повторный dry-run не обнаружил кандидатов. Скрипт проверяет, что абсолютный путь находится внутри проекта; отказывает при reparse target, предках и связанных потомках. Для повторной уборки сначала остановите тесты. Dry-run по умолчанию: `powershell -ExecutionPolicy Bypass -File .\clean.ps1`; удаление только с `-Apply`.

После приёмки удалены только созданные в этой работе Docker Compose проекты `aman-mvp-verification` и `aman-mvp-restore-check` с их временными тестовыми томами. Собранный образ `aman-bank-mvp:1.0.0` сохранён для запуска.

Рабочий `aman_bank.db`, `runtime`, `backups`, frontend dist и node_modules, исходные `anti_fraud_1`/`FraudBanc`, пользовательские датасеты и Python-окружение сохранены. Cache слои Docker не очищались глобальным prune. Локальные данные, `.env`, `.release` и кэши исключены из Git/релизного архива; Docker context дополнительно ограничен allowlist.
