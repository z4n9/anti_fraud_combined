# Архитектура целевой системы (anti_fraud_arrt)

## 1. Концептуальная схема
Единый модульный backend обслуживает как операции клиентов банка (клиентский шлюз), так и рабочее место аналитика (аналитический шлюз).

```mermaid
flowchart TB
    subgraph Clients["Пользовательские интерфейсы"]
        UI_AMAN["AMAN Bank Web UI<br/>(Клиенты / Родственники)"]
        UI_ANALYST["Risk Ledger Dashboard<br/>(Аналитики безопасности)"]
    end

    subgraph Backend["Единый FastAPI Backend (anti_fraud_arrt)"]
        direction TB
        
        subgraph Middlewares["Промежуточные слои"]
            AUTH_MID["Cookie Auth & Sessions (scrypt)"]
            SEC_MID["Security Headers & CORS/Origin Guard"]
        end

        subgraph Routers["API Routers"]
            R_AUTH["/api/auth (Login, Logout, CurrentUser)"]
            R_BANK["/api/cards, /api/transactions"]
            R_FAMILY["/api/family, /api/mock-egov, /api/trusted-invitations"]
            R_TRANSFERS["/api/transfers (Переводы + Pre-transfer Fraud Check)"]
            R_ANALYST["/api/analysis, /api/sources, /api/models (Кабинет аналитика)"]
        end

        subgraph CoreServices["Сервисный слой"]
            SRV_BANK["Bank Core (Атомарные списания, тиыны, балансы)"]
            SRV_EGOV["Mock eGov (Верификация родственных связей)"]
            SRV_ANTIFRAUD["Антифрод-пайплайн"]
        end

        subgraph AntifraudEngine["Модуль оценки рисков"]
            RULES["Движок правил (11 сценариев: суммы, всплески, дропперы, ночные)"]
            GRAPH["Ограниченный граф транзакций (BoundedTransactionGraph)"]
            ML_ADAPTER["ML Адаптер (CatBoost / Isolation Forest с fallback на правила)"]
            EXPLAIN["Генератор бизнес-объяснений (Русский язык)"]
        end

        subgraph Storage["Хранилище данных"]
            DB_BANK[("SQLite: Пользователи, карты, переводы, сессии")]
            DB_AUDIT[("SQLite / Staging: Журнал аудита рисков и анализов")]
        end
    end

    UI_AMAN --> AUTH_MID --> R_AUTH & R_BANK & R_FAMILY & R_TRANSFERS
    UI_ANALYST --> SEC_MID --> R_ANALYST

    R_TRANSFERS --> SRV_ANTIFRAUD
    SRV_ANTIFRAUD --> RULES & GRAPH & ML_ADAPTER
    RULES & ML_ADAPTER --> EXPLAIN
    R_TRANSFERS --> SRV_BANK
    R_FAMILY --> SRV_EGOV
    SRV_BANK --> DB_BANK
    R_ANALYST --> DB_AUDIT
```

---

## 2. Структура проекта в `anti_fraud_arrt`

```text
anti_fraud_arrt/
├── .agent/
│   └── skills/                         # Проектные навыки Antigravity
│       ├── project-integration/
│       ├── money-and-access/
│       └── antifraud-verification/
├── docs/                               # Контекст, архитектура, решения, прогресс
│   ├── project-context.md
│   ├── architecture.md
│   ├── integration-plan.md
│   ├── decisions.md
│   ├── testing.md
│   └── progress.md
├── backend/
│   ├── app/
│   │   ├── api/                        # API маршруты
│   │   │   ├── auth_routes.py
│   │   │   ├── bank_routes.py
│   │   │   ├── family_routes.py
│   │   │   ├── transfer_routes.py      # Включая pre-check и approve/reject
│   │   │   └── analyst_routes.py
│   │   ├── core/                       # Базовые настройки, безопасность, БД
│   │   │   ├── config.py
│   │   │   ├── database.py
│   │   │   └── security.py
│   │   ├── domain/                     # Схемы и модели
│   │   │   ├── models.py               # SQLAlchemy сущности
│   │   │   ├── schemas.py              # Pydantic схемы API
│   │   │   └── canonical.py            # Канонические признаки антифрода
│   │   ├── services/                   # Бизнес-логика
│   │   │   ├── bank_service.py
│   │   │   ├── family_service.py
│   │   │   ├── mock_egov.py
│   │   │   ├── fraud_detector.py       # Оркестратор антифрода
│   │   │   ├── transaction_rules.py    # Детерминированные правила
│   │   │   ├── transaction_graph.py    # Граф связей
│   │   │   └── explanations.py         # Объяснения факторов
│   │   ├── main.py                     # Единая точка входа FastAPI
│   │   ├── migrations.py               # Аддитивные миграции SQLite
│   │   └── seed.py                     # Тестовые данные (Амина, Марат, Алихан и др.)
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   └── conftest.py
│   ├── requirements.txt
│   └── pyproject.toml
├── frontend/
│   ├── aman/                           # Клиентский мобильный веб-интерфейс
│   │   ├── index.html
│   │   ├── style.css
│   │   └── app.js
│   └── analyst/                        # Кабинет аналитика Risk Ledger (React/TS)
│       ├── src/
│       ├── package.json
│       └── vite.config.ts
├── data/                               # Датасеты и синтетические выборки
├── AGENTS.md                           # Главная инструкция для агентов
└── README.md
```

---

## 3. Границы модулей и контракты взаимодействия

### Модуль переводов и антифрода
1. При вызове `POST /api/transfers`:
   - Запрос проходит валидацию суммы и получателя.
   - Вызывается `FraudDetector.evaluate_transfer(...)`.
   - Если риск = `low` или `medium`: операция исполняется атомарно.
   - Если риск = `high` или `critical`:
     - Проверяется `TrustedPerson` отправителя.
     - Если защиты нет или родственник не принял приглашение -> отказ (`403` / `422` с понятным описанием причины).
     - Если защита активна -> создание транзакции в статусе `pending_approval`. Средства не списываются.
2. Подтверждение родственником:
   - Эндпоинт `POST /api/transfers/{id}/approve` доступен ТОЛЬКО аутентифицированному доверенному лицу.
   - При одобрении: выполняется атомарное списание с карты отправителя и зачисление получателю.
   - Эндпоинт `POST /api/transfers/{id}/reject`: перевод помечается `rejected`.
