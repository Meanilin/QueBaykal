# Issues для QueBaykal

Сгенерировано автоматически из спецификации:
`/home/meanilin/Obsidian/Meanilin/Полное резюме проекта Telegram-бот для выбора фильма и управления общим телевизором.md`

Репозиторий: `Meanilin/QueBaykal`

Использовать: `gh issue create --label epic --title "..." --body "..."` для эпиков,
и `gh issue create --label "epic:0-foundation" --title "..." --body "..." --project "..."`
для подзадач (после создания проекта в GitHub).

Метки:
- `epic` — эпик
- `epic:0-foundation` … `epic:7-observability` — привязка к эпику
- `subtask` — подзадача

---

## Epic 0. Инфраструктура (foundation)

Базовый скелет: БД, настройки, FastAPI + aiogram, Redis, логирование, Docker, структура проекта.
Без этого ничего не запускается.

**Подзадачи:**

- 0.1 Структура проекта: пакеты `bot/`, `api/`, `core/`, `db/`, `models/`, `services/`, `agents/`
- 0.2 Pydantic Settings + `.env` (POSTGRES_*, REDIS_*, BOT_TOKEN, BACKEND_URL)
- 0.3 Alembic: начальная миграция, env.py с async engine
- 0.4 SQLAlchemy 2.0 async модели: `users`, `chats`, `chat_members`, `tv_devices`, `chat_tv_bindings`
- 0.5 FastAPI app: lifespan, роутеры, healthcheck `/health`
- 0.6 aiogram 3.x Dispatcher: подключён к FastAPI, polling/webhook
- 0.7 Redis FSM storage (aiogram-fsm-redis) + кеш
- 0.8 Логирование: structlog + correlation_id middleware
- 0.9 Dockerfile + docker-compose: `bot`, `api`, `db`, `redis`
- 0.10 `requirements.txt` / `pyproject.toml`: зафиксировать версии

## Epic 1. Booking

Модель `bookings` (12 статусов включая `overrun`), APScheduler в БД, recovery, коллизии.

**Подзадачи:**

- 1.1 Модель `bookings` + миграция
- 1.2 Команда `/book <YYYY-MM-DD HH:MM> [duration]` + парсер даты
- 1.3 Команда `/book now`
- 1.4 Команда `/my_bookings`
- 1.5 Команда `/cancel_booking <id>` — инициатор или admin
- 1.6 Команда `/confirm_booking <id>` за 1 час
- 1.7 Команда `/free_slots` — следующие 24ч
- 1.8 APScheduler + таблица `scheduled_jobs` (next_run, callback, payload)
- 1.9 Recovery: при старте бот восстанавливает jobs из БД
- 1.10 Авто-`expired` если `/confirm_booking` не получен за 30 мин до `booking_start`
- 1.11 Статус `overrun` + команда `/resolve_overrun <booking_id>`
- 1.12 Проверка коллизий при создании брони (пересечение с `active`)

## Epic 2. Voting (Telegram bot)

3 режима, state machine, реакции, top-N, финал, tiebreaker, /setup, права.

**Подзадачи:**

- 2.1 Модели `vote_sessions`, `event_movies`, `votes` + миграции
- 2.2 State machine: 11 состояний (CREATED → SUGGESTIONS → FILTER → FINAL_VOTE → WINNER_SELECTED → DOWNLOADING → READY → PLAYING → COMPLETED, плюс DOWNLOAD_FAILED, CANCELLED)
- 2.3 Команда `/start_instant_vote [suggest] [filter] [vote] [duration]`
- 2.4 Команда `/start_planned_vote <YYYY-MM-DD HH:MM>`
- 2.5 Команда `/suggest <title>` + дедуп в рамках сессии
- 2.6 Команда `/list` — inline-клавиатура
- 2.7 Fire reactions (🔥) → `reaction_count` на event_movie
- 2.8 Top-N отсев по `reaction_count` (default 5, `/set_top_n 2..10`)
- 2.9 Final vote: inline-клавиатура, UNIQUE(session_id, user_id)
- 2.10 Winner selection + tiebreaker `random.choice`
- 2.11 Команда `/next_step` — ручной переход между этапами
- 2.12 Команда `/end_vote` / `/end_suggest`
- 2.13 Команда `/retry_vote` — пересоздаёт финалистов
- 2.14 Команда `/set_min_votes` + проверка порога
- 2.15 Команды `/set_default_instant`, `/set_default_planned` (настройки чата)
- 2.16 Команда `/setup` — привязка чата к TV-устройству
- 2.17 Команды `/stop` / `/force_stop` (разграничение прав)
- 2.18 Privacy: фильтрация данных в личке (раздел 48 плана)
- 2.19 Управление admin-ролями (назначение, revoke)

## Epic 3. Downloader

`media_files`, `download_tasks` (6 статусов), fallback chain, авто-cancel, progress.

**Подзадачи:**

- 3.1 Модель `media_files` + миграция
- 3.2 Модель `download_tasks` + миграция (статусы: pending/running/paused/cancelled/completed/failed)
- 3.3 Downloader interface (Strategy): Local / yt-dlp / direct URL / qBittorrent
- 3.4 Local Media Library check — поиск по `checksum` в `media_files`
- 3.5 Команда `/provide_link <url>` — перевод сессии в DOWNLOAD_RETRY
- 3.6 Команда `/retry_vote` — fallback на fallback
- 3.7 Прогресс скачивания: редактируемое сообщение, 10% (fallback на 100%)
- 3.8 Авто-`cancelled` для предыдущего `download_task` при новом `/provide_link`
- 3.9 Сохранение `actual_movie_duration` (ffprobe/yt-dlp metadata)

## Epic 4. Windows Agent

Регистрация, polling, идемпотентность, медиа-индексация, watchdog.

**Подзадачи:**

- 4.1 Модель `agent_commands` + миграция
- 4.2 `GET /api/agent/poll?device_id=...` — идемпотентные команды
- 4.3 `POST /api/agent/report` — результат выполнения
- 4.4 `POST /api/agent/heartbeat` — watchdog input
- 4.5 `POST /api/agent/register` — первичная регистрация
- 4.6 `POST /api/agent/media_index` — загрузка индекса медиатеки
- 4.7 Конфиг-файл агента: `device_id`, `agent_token`, `tv_hostname`, `media_library_path`, `backend_url`
- 4.8 Watchdog процесс на backend (heartbeat > 30с → alert админу)
- 4.9 ffprobe / mediainfo на стороне агента → `media_index` payload

## Epic 5. Playback (VLC)

Команда агенту, выбор аудио/subtitles, conflict resolution, мониторинг процесса.

**Подзадачи:**

- 5.1 Команда `START_PLAYBACK <playback_command_id>` агенту
- 5.2 VLC CLI: `vlc --audio-language=ru,en <file>` + forced subtitle logic
- 5.3 Agent ждёт подтверждения `PLAYBACK_STARTED` от процесса VLC
- 5.4 VLC process monitor (agent → backend, every 5s)
- 5.5 `--sub-track=0` если есть forced, иначе `-1` (все)
- 5.6 Audio track selection по приоритету `ru > en > default`
- 5.7 Conflict resolution: если VLC уже запущен вручную — agent ждёт

## Epic 6. Notifications

Личные и групповые напоминания, уведомления о событиях, /set_reminders.

**Подзадачи:**

- 6.1 Личные напоминания (за 30 мин до `booking_start`)
- 6.2 Групповые напоминания (за 1 час до `booking_start`)
- 6.3 Уведомление о смене режима (planned → instant)
- 6.4 Уведомление о `overrun`
- 6.5 Команда `/set_reminders on|off` для админа чата
- 6.6 Напоминания о начале/конце голосования

## Epic 7. Observability & Audit

`audit_log`, корреляция логов, метрики, error tracking, /status.

**Подзадачи:**

- 7.1 Модель `audit_log` + миграция
- 7.2 Запись admin-событий (override, force_stop, resolve_overrun, provide_link)
- 7.3 Корреляция логов (request_id, session_id, booking_id)
- 7.4 Метрики Prometheus: votes count, downloads success/fail, agent offline
- 7.5 Sentry / error tracking
- 7.6 Команда `/status` — состояние системы для админа
