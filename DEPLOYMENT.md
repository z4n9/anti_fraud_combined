# Деплой конкурсного MVP

Один контейнер обслуживает Family Bank и Family Guard. Node нужен только на стадии сборки; runtime — Python 3.13, один Uvicorn worker, непривилегированный пользователь. Данные находятся в постоянном томе `/data`. Исходные проекты и локальная SQLite не входят в образ.

## Быстрый запуск

Установите Docker Desktop (Windows/macOS) или Docker Engine и Compose (Linux). На Windows запустите Docker Desktop с Linux containers. Выполняйте из корня anti_fraud_arrt:

```text
docker compose up --build -d --wait
docker compose ps
docker compose logs --tail 30 aman
```

Банк: http://127.0.0.1:8000/; аналитик: http://127.0.0.1:8000/analyst/. Готовность обоих модулей: http://127.0.0.1:8000/api/readiness (200 и status=ok). На новом томе создаются аккаунты из USER_GUIDE.md; старые данные не сбрасываются при старте.

Остановка: `docker compose down`. Последующий `up -d --wait` сохраняет том. `down -v` удаляет данные — для обычной остановки эту опцию не используйте. Обновление: после сохранения резервной копии выполните `docker compose up --build -d --wait` с новой версией кода. Миграции делают копии SQLite в `/data/backups` перед изменением схемы.

## Порт, публичный адрес и HTTPS

Необязательная настройка:

```powershell
Copy-Item deploy/.env.example deploy/.env
docker compose --env-file deploy/.env config --quiet
docker compose --env-file deploy/.env up --build -d --wait
```

На Linux копирование: `cp deploy/.env.example deploy/.env`. Файл `.env` не хранится в Git. При каждом запуске используйте тот же `--env-file`.

| Переменная | Назначение |
|---|---|
| AMAN_PORT | Порт хоста, по умолчанию 8000 |
| AMAN_BIND_ADDRESS | Интерфейс хоста, по умолчанию 127.0.0.1 |
| AMAN_PUBLIC_ORIGIN | Точный адрес браузера, например https://demo.example.org, без пути; пустой — локальная проверка по адресу запроса |
| AMAN_COOKIE_SECURE | Для HTTPS true; HTTPS public origin принудительно включает Secure cookie |
| AMAN_TRUSTED_PROXY_IPS | Только IP/CIDR фактического reverse proxy; по умолчанию 127.0.0.1. `*` и сети `/0` запрещены |

Если задан публичный origin, открывайте **именно этот адрес**: localhost и 127.0.0.1 — разные origin. При HTTPS поставьте reverse proxy с действительным сертификатом перед localhost-портом. Он должен сохранять Host и передавать X-Forwarded-Proto=https. Укажите IP, который backend фактически видит как адрес proxy (проверьте логи контейнера); адрес шлюза Docker может отличаться от 127.0.0.1. Прокси и приложение должны работать на одном публичном origin. Не доверяйте всем адресам для обхода ошибок настройки.

Это конкурсная демонстрация с публичными паролями. Для закрытого показа на сервере ограничьте доступ на reverse proxy или через VPN; реальные клиентские данные сюда не загружайте. Публикация на конкретной площадке не выполняется автоматически: этот релиз готовит переносимый образ и конфигурацию.

Рекомендованный механизм одно-серверного запуска описан в [официальной документации Docker Compose](https://docs.docker.com/compose/how-tos/production/).

## Постоянные данные и резервная копия

Том `<compose-project>_aman-data` содержит SQLite, миграционные копии, аналитические загрузки/результаты, реестр моделей и семантический словарь. Его не заменяет локальный aman_bank.db. Не удаляйте volume для обновления приложения.

Для согласованной копии остановите сервис, скопируйте **всё** `/data` и запустите снова:

```powershell
docker compose stop aman
New-Item -ItemType Directory -Force backups/mvp-snapshot
docker compose cp aman:/data/. backups/mvp-snapshot/
docker compose start aman
```

На Linux создание папки: `mkdir -p backups/mvp-snapshot`. Используйте новую папку для каждой копии. Для выбранного имени проекта/файла окружения добавляйте те же `-p` и `--env-file` во все команды. Остановка нужна для согласованности SQLite и аналитических файлов; копия одной БД не сохраняет загруженные анализы.

Восстановление выполняйте в остановленный сервис: `docker compose cp backups/mvp-snapshot/. aman:/data/`. После переноса с Windows владельцы файлов могут измениться: отдельный служебный контейнер `docker compose run --rm --no-deps --user 0 --cap-add CHOWN aman python -c "import os; [(os.chown(p,10001,10001), [os.chown(os.path.join(p,n),10001,10001) for n in ds+fs]) for p,ds,fs in os.walk('/data')]"` восстанавливает владельца данных, после чего `docker compose start aman`. Старые файлы, которых нет в снимке, этой командой не удаляются; полное восстановление лучше делать в новый пустой том отдельного проекта.

## Временное хранилище загрузок

Multipart-загрузка сначала принимается целиком во временный файл, затем переносится в аналитическое хранилище. `TMPDIR=/data/upload-tmp` создаётся при старте от пользователя приложения и использует диск тома, а не `/tmp` (tmpfs 256 МиБ). Это позволяет принять CSV размером 350 МиБ при лимите файла 500 МиБ. На время приёма и копирования нужны свободные примерно две величины файла плюс место для результатов и банковской базы. Временные файлы закрываются и удаляются после обработки запроса; каталог приватен и не раздаётся HTTP.

После обновления выполните `docker compose up --build -d --wait`: пересоздание контейнера сохраняет существующий том. Изменение размера временного хранилища не отменяет лимит 1 000 000 записей и требования к распознаваемым полям. Для датасетов с числовым `step` вместо даты нужно документированное преобразование времени; произвольная замена `step` датой недопустима.

## Репетиция с отдельной базой

Новое имя Compose-проекта создаёт отдельный том. Имя должно быть новым для чистой репетиции; повтор прежнего имени сохраняет его данные. PowerShell:

```powershell
$env:AMAN_PORT='8001'
docker compose -p aman-rehearsal-01 up -d --wait
```

Linux: `AMAN_PORT=8001 docker compose -p aman-rehearsal-01 up -d --wait`. Откройте http://127.0.0.1:8001/. Для остановки используйте то же имя проекта: `docker compose -p aman-rehearsal-01 down`. Основной том и локальная рабочая база сохраняются.

## Автоматический демонстрационный сценарий

Запускайте на **репетиционной** базе: сценарий меняет тестовые балансы, согласия и создаёт анализ. Он не сбрасывает данные. Нужен Python >=3.12 на машине проверки; дополнительных пакетов скрипт не требует.

```text
python scripts/smoke_demo.py --base-url http://127.0.0.1:8001 --confirm-demo-writes --state-out .test-tmp/rehearsal-state.json
docker compose -p aman-rehearsal-01 up -d --force-recreate --wait
python scripts/smoke_demo.py --base-url http://127.0.0.1:8001 --verify-state .test-tmp/rehearsal-state.json
```

Для второй Compose-команды сохраните AMAN_PORT=8001; без него порт будет другим. Скрипт проверяет семейный конфликт, окончательное решение сотрудника, отсутствие двойного списания, AntiScam, аналитический импорт и сохранность состояния после пересоздания.

## Публичное демо через Cloudflare Quick Tunnel (Windows)

Настроено 21 октября 2026. Приложение остаётся в локальном Docker; компьютер,
Docker Desktop, интернет и процесс `cloudflared` должны работать всё время показа.
Это временная ссылка: при новом запуске Tunnel адрес меняется. Аккаунт и домен
Cloudflare не нужны. Текущий адрес сохранён в `runtime/cloudflare/url.txt`.

Бинарник `runtime/cloudflare/cloudflared.exe` скачан из официального репозитория
Cloudflare. Логи, PID, адрес и настройки находятся в игнорируемой Git папке
`runtime/cloudflare/`. Не добавляйте эту папку в коммит.

Для нового запуска из `anti_fraud_arrt` откройте PowerShell и оставьте его работающим:

```powershell
.\runtime\cloudflare\cloudflared.exe tunnel --url http://127.0.0.1:8000 --no-autoupdate --protocol http2
```

Скопируйте выданный HTTPS-адрес. Во втором окне PowerShell примените его:

```powershell
$env:AMAN_PUBLIC_ORIGIN = 'https://НОВЫЙ-АДРЕС.trycloudflare.com'
$env:AMAN_COOKIE_SECURE = 'true'
docker compose up -d --wait
```

Точный origin обязателен для входа и других POST-запросов; wildcard не используйте.
Пересоздание контейнера сохраняет существующий том `anti_fraud_arrt_aman-data`.
После настройки открывайте банк и `/analyst/` по HTTPS-ссылке. Локальный HTTP-вход
при включённых Secure cookies использовать не следует.

Для уже запущенного фонового туннеля PID хранится в `runtime/cloudflare/tunnel.pid`.
Остановить именно его, проверив имя процесса:

```powershell
$tunnelPid = [int](Get-Content runtime/cloudflare/tunnel.pid)
$tunnelProcess = Get-Process -Id $tunnelPid -ErrorAction SilentlyContinue
if ($tunnelProcess -and $tunnelProcess.ProcessName -eq 'cloudflared') {
    Stop-Process -Id $tunnelPid
}
```

Для возврата к локальному HTTP-доступу из PowerShell:

```powershell
$env:AMAN_PUBLIC_ORIGIN = 'http://127.0.0.1:8000'
$env:AMAN_COOKIE_SECURE = 'false'
docker compose up -d --wait
```

Ссылка доступна всем, кто её знает; банковская и аналитическая авторизация остаются
обязательными. Используйте только демонстрационные данные. На бесплатном Cloudflare
лимит тела загрузки — 100 МБ: для аналитика используйте небольшие CSV-примеры.
У Quick Tunnel нет гарантий доступности и поддержки SSE.

Документация: https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/

## Офлайн-показ и перенос образа

После первой успешной сборки сохраните образ:

```text
docker save -o aman-bank-mvp-1.0.0.tar family-bank-mvp:1.0.0
```

На компьютере с Docker: `docker load -i aman-bank-mvp-1.0.0.tar`, затем `docker compose up -d --no-build --wait` из папки с compose.yaml. Готовому контейнеру Node/Python на хосте и интернет для запуска не нужны. Архитектура компьютера должна совпадать с сохранённым образом (проверенный образ — linux/amd64). Новый том создаёт свежие тестовые аккаунты; для прежнего состояния отдельно перенесите резервную копию `/data`.
