# Архитектура и стек

Статус: действующая спецификация (план), редакция 16 сентября 2026.
Перенесена без изменения технических решений 24 сентября 2026 из §25 старого плана.
[Индекс решений](index.md) · [Состояние реализации](../plans/website-implementation-progress.md)

## 25. Техническая спецификация реализации — 16 сентября 2026

Этот раздел добавлен после изучения §§1–24, [концепта](../plans/website-design-concept.md),
[HTML-прототипа](../plans/website-design-prototype.html) и действующего кода бота. Это рабочая спецификация:
решения ниже имеют приоритет при расхождении с предыдущими предложениями и макетами. Предыдущий
текст сохранён. Названия новых файлов, моделей и команд ниже — задания на реализацию, а не уже
существующие возможности проекта.

Сохраняются решения владельца из §24.1: полный релиз A, пять языков, бюджет €50/месяц, актуальная
линейка Django, бот пока работает с SQLite в GitHub Actions. Аккаунты читателей и обсуждения
появляются в B/C. Регистрацию сотрудников, права и MFA делаем уже в A.

Навигация по спецификации:

- [Стек и исправления прежних предположений](architecture.md#251-решения-которые-больше-не-нужно-выбирать-при-реализации)
- [Код, Django, Wagtail и библиотеки](architecture.md#253-структура-кода-и-границы-ответственности)
- [Данные и импорт](data.md#255-данные-сайта-и-протокол-полного-импорта)
- [Наблюдение за делами](data.md#256-актуальность-необходимая-работа-в-боте-до-запуска-сайта)
- [Переводы и поиск](data.md#257-перевод-правки-и-редакторская-работа)
- [Очередь и расписание](data.md#259-фоновые-задачи-и-расписание)
- [Деплой и откат](deployment.md#2510-деплой-воспроизводимый-сервер-и-неизменяемый-релиз)
- [Backup и аварии](deployment.md#2511-backup-наблюдаемость-и-аварийные-действия)
- [Среда разработчика](deployment.md#2512-среда-разработки-и-команды-которые-экономят-время)
- [Ревизия дизайна](delivery.md#2513-критическая-ревизия-дизайна)
- [Проверки и задачи](delivery.md#2514-проверки-и-quality-gate)
- [Релизы B/C](delivery.md#2516-как-bc-добавляются-без-перестройки-a)

### 25.1. Решения, которые больше не нужно выбирать при реализации

| Область | Решение | Основание / граница |
| --- | --- | --- |
| Приложение | Django 6.1.x + Wagtail 8.0.x, Python 3.14.x | Один Python-стек, готовая CMS; точные patch-версии фиксирует проверенный `uv.lock` |
| Публичный интерфейс | Django Templates, собственный CSS, небольшой ES module | Все маршруты, поиск и фильтры работают без JS |
| База сайта | PostgreSQL 17.x, одна БД с Django migrations | Консервативная поддерживаемая ветка; обновление major — отдельная операция |
| CMS | Wagtail Pages для материалов, snippets для редакторских объяснений дел | Процесс законодательства не моделируется деревом страниц |
| Очередь | `django.tasks` + `django-tasks-db`, один worker | PostgreSQL уже есть; Redis не требуется |
| Планировщик | systemd timers на хосте | Один источник расписания, блокировки, журнал, воспроизводимое восстановление |
| Web runtime | Gunicorn WSGI | WebSockets и длительные HTTP-задачи отсутствуют; ASGI пока не даёт пользы |
| Сервер | VPS в ЕС, исходная цель 2 vCPU / 4 GB; x86_64 | ARM выбирать только по полной цене и проверенным wheels, а не по предположению «дешевле» |
| Развёртывание | Docker Compose для приложения/БД, Caddy как systemd service хоста | Два web-контейнера и явное переключение; Caddy не получает Docker socket |
| Артефакт | Один образ web/worker/management commands в GHCR, по digest | Собирается и проверяется в CI, повторно не собирается на VPS |
| Статика / медиа | Версионированная статика, файловые media на постоянном volume | Правила смены релиза и доступа описаны ниже; S3 для media пока не нужен |
| Репозиторий | Существующий monorepo, отдельный пакет `web/` в uv workspace | Бот не устанавливает Django и не импортирует ORM сайта |
| Импорт | Pull полного снимка `state`, проверка и атомарная активация поколения | Без входящего webhook, без исполнения SQLite SQL в PostgreSQL |
| Доставка сообщений | Существующий бот; будущий email через отдельный outbox | Импорт, перевод и deploy сами по себе не публикуют читателям |

Совместимость Django 6.1 с Python 3.14 и поддержка Django 6.1 в Wagtail 8.0 подтверждены
[Django](https://docs.djangoproject.com/en/6.1/releases/6.1/) и
[Wagtail](https://docs.wagtail.org/en/stable/releases/8.0.html). Это не подтверждает совместимость
всех дополнений: установка, строгая типизация, MFA, миграции и настоящий worker входят в первый
технический прототип. Нельзя считать чтение release notes успешным прототипом.

В §24.4 ARM назван более дешёвым. Проверенная таблица для DE/FI показывает CX23 €5.49 и CAX11
€5.99 без VAT/IPv4; автоматически предпочитать CAX оснований нет. CX33 — €8.49, если замер двух
web-процессов, worker и БД потребует больше памяти. Проверить доступность и итоговую корзину перед
покупкой. [Таблица Hetzner](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/).
Рабочий конверт остаётся €50: инфраструктура с доменом и backup ориентировочно €15–25, остаток —
LLM и запас. Это бюджет проекта, не новая гарантия тарифов или ранее оценённой стоимости переводов.

### 25.2. Поправки к предыдущим техническим обещаниям

| Ранее | Уточнение для реализации |
| --- | --- |
| Полный импорт безопасен «по определению» (§24.3.2) | Нужны lock, фиксированный commit, проверка порядка, транзакция и тест прерывания. Два полных импорта тоже могут завершиться в обратном порядке |
| Переключение staging-схемы | Не переименовывать PostgreSQL-схемы и не пересоздавать таблицы с FK редактора. Использовать поколения фактов и постоянные сущности (§25.5) |
| 93 `analyzed` = 93 актуальные страницы | Число — снимок предыдущего исследования. Связанные строки, качество анализа и режим наблюдения проверяются отдельно |
| Один `TranslatableMixin` решает перевод | Он связывает локали; очередь, защищённые поля, устаревание, сравнение и публикация — наша логика |
| У карточки вне Page обязательно свой preview | Wagtail snippets поддерживают `PreviewableMixin`; применяем его к объяснению, не строим вторую CMS |
| Wagtail Axe запрещает публикацию без alt | Проверка редактора не заменяет серверную валидацию. Нужные ограничения проверять при каждой публикации, включая задачу/CLI |
| Форму сообщения об ошибке закрывает `wagtail.contrib.forms` без кода | Привязка к делу/ревизии, антиспам, права и обработка требуют своей небольшой Django Form и модели |
| Wagtail audit полностью заменяет аудит предметных данных | UI-действия логировать штатно; основания merge/split и коррекции факта сохранять в предметном журнале |
| `FETCH_PEERS` устраняет N+1 автоматически | Выбирать режим явно; держать `select_related`/`prefetch_related` и проверять число запросов |
| Любой `ADD COLUMN DEFAULT` мгновенный | Быстрый путь зависит от default; DDL всё равно может ждать блокировку. `default` Python и `db_default` SQL — разные вещи |
| После неудачной миграции сайт «не тронут» | Ранее выполненные миграции остаются; старый код уже работает с изменённой БД |
| Откат всегда занимает секунды | Только если старый процесс ещё жив и совместимы схема, данные, статика и формат заданий |
| Staging бесплатный | Образ повторно используется, но PostgreSQL, worker и процессы занимают память и диск |

Основания: [возможности snippets](https://docs.wagtail.org/en/stable/topics/snippets/features.html),
[транзакции Django](https://docs.djangoproject.com/en/6.1/topics/db/transactions/),
[PostgreSQL ALTER TABLE](https://www.postgresql.org/docs/17/sql-altertable.html).

### 25.3. Структура кода и границы ответственности

```text
pyproject.toml / uv.lock             корневой пакет бота и общий lock workspace
src/lexinform/                      существующая предметная логика и pipeline
web/
  pyproject.toml                    пакет lexinform-web, workspace-зависимость lexinform
  manage.py
  src/lexinform_web/
    config/                        settings: base, local, test, production; urls, wsgi
    accounts/                      User, staff auth, MFA; в B — настройки читателя
    matters/                       Matter, идентичности, факты, события, selectors/services
    ingestion/                     контракт снимка, адаптер SQLite, импорт и отчёт
    editorial/                     Page-типы, blocks, snippets, overrides, wagtail_hooks
    translations/                  заявки, провайдер, glossary, validation, tasks
    search/                        поисковая проекция, выдача, маршруты и фильтры
    feedback/                      форма исправления и редакторская очередь
    operations/                    health, release metadata, команды и диагностические отчёты
    templates/                     layouts, components, pages, blocks, wagtailadmin
    static/                        css, js, fonts, brand
    locale/                        pl, en, be, uk, ru; gettext-каталоги интерфейса
  tests/                           unit, scenario, db, e2e; свой WebWorld
  fixtures/                        маленький синтетический корпус и ожидаемые результаты
scripts/                           bootstrap, check, smoke, fixture/export helpers
deploy/web/                        Dockerfile, compose, Caddy fragment, deploy/backup scripts
deploy/ansible/                    идемпотентная настройка хоста, без секретов в inventory
docs/operations/                   bootstrap, deploy, rollback, restore, incident runbooks
```

Использовать [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/): root остаётся
пакетом `lexinform`, web — отдельным member с зависимостью на root через `workspace = true`.
Штатные команды бота продолжают работать; web запускается через `uv run --package lexinform-web`.
Проверить установку обоих deployment targets по одному lock, включая отсутствие Django в образе
реле. Никаких обращений Django к рабочему SQLite-файлу бота.

Внутри web: views/forms/tasks вызывают services; selectors собирают чтение ORM; адаптеры импортируют
внешние данные и вызывают LLM. Шаблоны получают типизированные display-модели, не запускают запросы
к Sejm/RCL и не вычисляют правовые состояния по строкам. Web может импортировать публичные
`lexinform.models`, чистые helpers и `Clock`; обратная зависимость запрещена. Сервисы бота не
получают `request`, Django Model или настройки Wagtail.

`next_phase`, расчёт окон и выбор существенных событий переиспользовать через чистое представление
данных. Сейчас формулировки частично связаны с Telegram: вынести общую структуру результата, а
Telegram HTML и web HTML рендерить отдельно. Не парсить готовую Telegram-карточку и не копировать
правила в template tags. Бизнес-логика времени получает `Clock`; UTC хранится в БД, сроки и
интерфейс используют `Europe/Warsaw`.

### 25.4. Что конкретно берём из Django и Wagtail

#### Django

- `auth`, `contenttypes`, `sessions`, `messages`, `staticfiles`, `postgres`, `sitemaps`; `sites`
  только если нужен выбранной конфигурации auth. `syndication` — для RSS после основного A.
- Своя `User(AbstractUser)` **в первой миграции**, до создания FK Wagtail. Сначала сотрудники;
  уникальный нормализованный email, язык интерфейса, публичный псевдоним пригодятся в B.
- ORM, `transaction.atomic`, `UniqueConstraint`/`CheckConstraint`, формы, CSRF, стандартные
  permissions, пагинация и class/function views без обязательного DRF.
- `gettext_lazy`, `{% translate %}`/`blocktranslate`, `LocaleMiddleware`, `i18n_patterns` для пяти
  публичных префиксов. Строки бота в `i18n.py` сохраняются; web не расширяет вручную его Labels на
  сотни строк интерфейса. Терминологию синхронизировать тестируемым glossary.
- `django.contrib.postgres.search`, `GinIndex`, миграция `TrigramExtension`; `btree_gin` добавлять
  только при конкретном индексе, которому оно нужно. Для обычной выдачи оно не требуется.
- `django.tasks`, CSP и template partials; новый `MAILERS` для конфигурации писем. Эти возможности
  не требуют отдельного `django-csp`, `django-template-partials` или собственного task API.
- Не публиковать Django admin параллельно Wagtail. Не устанавливать DRF, GraphQL, Channels,
  Celery, Redis, Elasticsearch или SPA framework без новой задачи, которая их требует.

#### Wagtail

Базовые приложения брать из штатной конфигурации Wagtail, включая его транзитивные зависимости
`modelcluster`/`taggit`; не удалять системные компоненты ради искусственно короткого списка.
Подключить `wagtail.locales`, `wagtail.contrib.settings`, `redirects`, `simple_translation` и
`sitemaps`. Маршруты дел, поиска и служебных функций регистрировать перед catch-all Wagtail.
[Интеграция Wagtail](https://docs.wagtail.org/en/stable/getting_started/integrating_into_django.html).

| Задача | Реализация |
| --- | --- |
| Главная и материалы | `HomePage`, `GuideIndexPage`, `GuidePage`, `ArticleIndexPage`, `ArticlePage`, `InformationPage`; ограничить `parent_page_types`/`subpage_types` |
| Гайд | `verified_at`, `review_due_at`, автор/проверивший, архив/замена, структурированное тело; дата вычитки не меняется при правке SEO |
| Редакционное объяснение дела | `MatterContent` как snippet с `TranslatableMixin`, `RevisionMixin`, `DraftStateMixin`, `PreviewableMixin`; порядок mixins по документации |
| Предпросмотр дела | `get_preview_template`/`get_preview_context`: текущие факты + выбранная редакция объяснения; сессия и права сотрудника, `no-store` |
| Роли | Группы Editor / Reviewer / Administrator; publish, fact correction и merge — отдельные разрешения |
| Переводы | `Locale`, `translation_key`, `simple_translation` для копии; собственный сервис заполняет draft revision |
| Темы и закрепления | `Topic` + локализованные labels, `Pin` с областью и интервалом; `SnippetViewSet` и chooser |
| Импорт/свежесть | Собственные read-only `ModelViewSet`/reports и панели главной Wagtail |
| Ревизии и публикация | Wagtail revisions как редакторский механизм; отдельный неизменяемый публичный snapshot для истории карточки |
| По расписанию | `publish_scheduled` через timer; просто наличие `go_live_at` ничего не запускает |
| Медиа | Wagtail Images с фиксированными rendition specs; Documents только для собственных файлов, не зеркало PDF источников |
| Аудит | Wagtail `log()` для действий пользователя + `MatterDecision` для оснований предметных изменений |

Preview/revisions у snippets требуют явного включения mixins; их наличие не делает любую Django
Model готовой CMS. Источники: [snippets](https://docs.wagtail.org/en/stable/topics/snippets/features.html),
[simple_translation](https://docs.wagtail.org/en/stable/reference/contrib/simple_translation.html),
[команды Wagtail](https://docs.wagtail.org/en/stable/reference/management_commands.html).

StreamField гайдов/статей: paragraph с ограниченным rich text, heading H2/H3, list, steps,
legal_quote с URL источника, note, warning, table с заголовками, image с alt/decorative,
PolishTemplateBlock, RelatedMatterBlock, RelatedPageBlock. Связь с делом — chooser постоянного
`Matter`, с материалом — Page chooser с разрешением по `translation_key` в язык читателя.
Не считать, что chooser сам исправляет любую ссылку на нужную локаль. В отсутствие перевода
подписывать язык целевой страницы.

Для служебных таблиц API не нужен: read-only viewsets и небольшие явные действия POST. Не включать
`wagtail.contrib.forms`, `search_promotions`, frontend cache invalidation, API v2 и
`RoutablePageMixin` просто потому, что они существуют. История дела — Django route; формы ошибок
— `feedback`; CDN пока не кэширует HTML. Workflow moderation включить для гайдов и проверенных
переводов, если действительно есть reviewer; один редактор может публиковать сам, без фиктивного
«второго согласования».

#### Прямые зависимости

| Пакет | Когда | Для чего / условие |
| --- | --- | --- |
| `Django`, `wagtail`, `psycopg[binary]`, `gunicorn`, `whitenoise` | A | ORM/CMS, PostgreSQL, WSGI, manifest/compressed static; wheels проверять в Linux target |
| `django-tasks-db` | A | Backend `django_tasks_db.DatabaseBackend`, worker `db_worker`; проверить kill/restart и upgrade |
| `django-allauth[mfa]` | A | Один механизм staff login, TOTP и recovery codes; reader signup выключен до B |
| `pydantic-settings`, `pydantic`, `anthropic` | A | Уже используемый подход к settings/контрактам/LLM, без LangChain |
| `Pillow` | A | Обработка изображений, обычно приходит через Wagtail; pin через lock |
| `pytest-django`, `django-stubs[compatible-mypy]` | dev | PostgreSQL-тесты и строгая типизация Django; версии согласовать с mypy бота |
| `pytest-playwright`, `@axe-core/playwright` | dev/CI | Браузерные сценарии и доступность; Node нужен только тестам |
| `djlint`, `django-migration-linter`, `pip-audit` | dev/CI | Шаблоны, опасные SQL-миграции, известные уязвимости |
| `django-debug-toolbar` | local, опционально | Число запросов и SQL; не попадает в production settings |
| `markdown-it-py` | одноразовый импорт гайдов | Разобрать Markdown в блоки, не рендерить произвольный HTML |
| `django-storages`, `django-anymail` | позже | Только при выборе S3/media или конкретного email API |

У MFA нет обещания «поставили пакет — Wagtail защищён»: нужен единый login flow, запрет обхода через
штатный Wagtail login, обязательная повторная проверка для staff, защищённые recovery/reset и
тест доступа к preview/chooser/upload. Allauth предоставляет MFA, интеграция с правами Wagtail —
наша работа. [Allauth MFA](https://docs.allauth.org/en/latest/mfa/index.html).

В WEB-02 проверить типизацию Wagtail на реальных Page/StreamField/snippet. При неполных upstream
типах — узкие локальные `.pyi` для используемых границ и типизированные wrappers; не выключать
strict для всего web и не разбрасывать `type: ignore`. Mypy бота и Django запускать отдельными
конфигами, чтобы Django plugin не требовал PostgreSQL для проверки чистых моделей бота.
