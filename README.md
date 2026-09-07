# Cinema Queue Bot

Telegram-бот для **выбора фильма голосованием** и **управления общим телевизором** (Windows TV Agent + VLC).

## Архитектура

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Telegram  │◄───►│   Bot/API   │◄───►│  PostgreSQL │
│   (users)   │     │  (FastAPI)  │     │  + Redis    │
└─────────────┘     └──────┬──────┘     └─────────────┘
                           │
                    ┌──────▼──────┐
                    │ TV Agent    │
                    │ (Windows)   │
                    │  + VLC      │
                    └─────────────┘
```

**Компоненты:**
- **Bot** (`bot/`) — aiogram 3.x, polling/webhook, middleware (correlation_id, user context)
- **API** (`api/`) — FastAPI, `/health`, `/api/agent/*` для TV Agent
- **Models** (`models/`) — SQLAlchemy 2.0 async, 12 таблиц
- **Services** (`services/`) — бизнес-логика (bookings, voting, downloader, tv_delivery, notifications, scheduler, observability)
- **TV Agent** (`agent/`) — отдельный Windows-сервис, опрашивает команды, управляет VLC

## Стек

- Python 3.12+
- aiogram 3.x, FastAPI, SQLAlchemy 2.0, APScheduler
- yt-dlp (скачивание), VLC (проигрывание)
- PostgreSQL + Redis
- Docker / docker-compose

## Быстрый старт (local dev)

```bash
# 1. Клонировать
git clone https://github.com/Meanilin/QueBaykal.git
cd QueBaykal

# 2. Виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

# 3. Настройка .env
cp .env.example .env
# Заполните: BOT_TOKEN, POSTGRES_*, REDIS_*, ADMIN_IDS, SECRET_KEY

# 4. Поднять инфраструктуру
docker compose up -d postgres redis

# 5. Миграции
alembic upgrade head

# 6. Запуск бота + API
python -m bot          # терминал 1
python -m api          # терминал 2 (FastAPI на :8000)
```

## TV Agent (Windows)

```powershell
# На Windows машине с TV
git clone https://github.com/Meanilin/QueBaykal.git
cd QueBaykal/agent

# Настроить config.yaml (device_id, agent_token, backend_url, media_root, vlc_path)
copy config.yaml.example config.yaml
notepad config.yaml

# Установить как сервис (PowerShell от админа)
.\install_service.ps1

# Или запуск вручную
python main.py
```

## Переменные окружения (.env)

| Переменная | Описание |
|------------|----------|
| `BOT_TOKEN` | Telegram Bot API token (@BotFather) |
| `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | БД |
| `REDIS_HOST` / `REDIS_PORT` | Redis (FSM storage, cache) |
| `ADMIN_IDS` | CSV список Telegram user_id с root-правами |
| `SECRET_KEY` | Подпись JWT для agent токенов |
| `BACKEND_URL` | Базовый URL API (для agent polling) |
| `SENTRY_DSN` | (опц.) Sentry DSN |
| `ENVIRONMENT` | `dev` \| `prod` |

## Основные команды бота

### Booking (бронирование TV)
| Команда | Описание |
|---------|----------|
| `/book <YYYY-MM-DD HH:MM> [duration]` | Создать бронь (планируемая) |
| `/book now [duration]` | Мгновенная бронь |
| `/my_bookings` | Мои брони |
| `/cancel_booking <id>` | Отменить свою бронь |
| `/confirm_booking <id>` | Подтвердить за 1ч до старта |
| `/free_slots` | Свободные слоты на 24ч |
| `/resolve_overrun <id>` | Админ: разрешить оверран |

### Voting (голосование за фильм)
| Команда | Описание |
|---------|----------|
| `/start_instant_vote [suggest] [filter] [vote] [dur]` | Мгновенное голосование |
| `/start_planned_vote <YYYY-MM-DD HH:MM>` | Планируемое |
| `/suggest <title>` | Предложить фильм |
| `/list` | Список предложений (inline) |
| `/vote` | Финальное голосование (inline кнопки) |
| `/end_vote` / `/end_suggest` | Завершить этап |
| `/cancel_vote` | Отменить сессию |
| `/retry_vote` | Перезапуск финального этапа |
| `/vote_status` | Статус сессии |
| `/vote_sessions` | Все сессии чата (админ) |

### Downloader / Playback
| Команда | Описание |
|---------|----------|
| `/provide_link <url>` | Дать ссылку на скачивание (при DOWNLOAD_FAILED) |
| `/play` | Запустить воспроизведение на TV |
| `/stop` | Остановить |
| `/seek <hh:mm:ss>` | Перемотать |
| `/media_list` | Медиатека агента |
| `/media_index` | Переиндексация |

### Notifications
| Команда | Описание |
|---------|----------|
| `/set_reminders on\|off` | Админ: включить/выключить напоминания |

### Observability
| Команда | Описание |
|---------|----------|
| `/status` | Состояние системы (админ) |
| `/audit_log [limit] [action]` | Audit log (админ) |
| `/metrics` | Prometheus метрики (админ) |

## Структура БД (12 таблиц)

```
users                   — Telegram пользователи
chats                   — Чаты/группы
chat_members            — Участники + роли (member/admin/root)
tv_devices              — TV устройства (Windows Agent)
chat_tv_bindings        — Привязка чата к TV
bookings                — Брони TV (8 статусов)
scheduled_jobs          — APScheduler jobs (persisted)
vote_sessions           — Сессии голосования (11 состояний)
event_movies            — Предложенные фильмы в сессии
votes                   — Голоса (fire 🔥 / final)
media_files             — Метаданные скачанных файлов
audit_log               — Админские действия (17 типов)
```

## Деплой (production)

```bash
# На сервере
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Или systemd units для bot + api
# TV Agent — установлен как Windows Service
```

## Тесты

```bash
pytest tests/ -v
# 6 smoke tests: settings, logging, exceptions, models, bot_factory, api_health
```

## Полезное

- `alembic revision --autogenerate -m "msg"` — новая миграция
- `python scripts/gen_migration.py` — генерация из моделей
- `gh issue list --repo Meanilin/QueBaykal` — список задач

---

**Статус:** Все 7 эпиков реализованы, тесты зелёные, `master` готов к деплою.