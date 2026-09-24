# Деплой и эксплуатация сайта

Статус: действующая спецификация (план), редакция 16 сентября 2026.
Перенесена без изменения технических решений 24 сентября 2026 из §25 старого плана.
[Индекс решений](index.md) · [Состояние реализации](../plans/website-implementation-progress.md)

### 25.10. Деплой: воспроизводимый сервер и неизменяемый релиз

#### Топология и первичная установка

```text
Internet :443 → Caddy на хосте → 127.0.0.1:8101 (blue) или :8102 (green)
                               web: Gunicorn, тот же image digest
                               worker: один db_worker
                               PostgreSQL: только private Docker network
                               media: постоянный volume
systemd timers → wrapper → одноразовая command из active image
GitHub Actions → GHCR → ограниченная SSH-команда deploy
backup → restic → независимое хранилище
```

Docker Compose выбран вместо дополнительного освоения Podman/Quadlet; повторно выбирать runtime
в WEB-05 не нужно. Debian stable/Ubuntu LTS с фиксированной поддерживаемой версией, firewall,
NTP, обновления безопасности и log rotation настраиваются Ansible. Его inventory хранит только
несекретные параметры. Сервер не требует checkout приложения; host scripts и compose доставляются
версированным deployment bundle. Смена самого deploy-механизма — отдельное контролируемое обновление,
не неявный `git pull` внутри выкладки.

Хост: `/srv/lexinform-web/` с release manifests, static release directories и state; конфигурация
и секреты в `/etc/lexinform-web/` с ограниченными правами; данные PostgreSQL/media — отдельные
volumes. `.env` существующего бота не копируется и не используется как источник web settings.
Наружу открыты 80/443 и ограниченный SSH; Gunicorn порты только loopback, PostgreSQL без publish.

Непривилегированный пользователь внутри image, `read_only` root FS с явно выделенными writable
media/tmp, dropped capabilities, `no-new-privileges`, лимиты памяти/PIDs и размера логов. У каждой
роли свой DB credential: runtime DML, migrator DDL, backup read. Только deployment wrapper получает
узкую возможность управлять runtime; пользователь с доступом к Docker socket фактически имеет
root-права, поэтому просто назвать его `deploy` недостаточно. SSH key: forced command,
`no-port-forwarding`, `no-agent-forwarding`, `no-pty`, проверенный host key; никаких shell `eval`
над `SSH_ORIGINAL_COMMAND`.

Образы app/PostgreSQL и версия Caddy фиксируются; CI обновляет их отдельными проверяемыми PR.
Для web первоначально два Gunicorn worker, умеренные timeout/graceful timeout. Бюджет на 4 GB
проверить нагрузкой: PostgreSQL около 1 GB, blue/green до 650–750 MiB каждый, task worker до
500–650 MiB, остальное — ОС/proxy/cache. Это начальные лимиты для замера, не оценка фактического RSS.
При недостатке памяти перейти на 8 GB до запуска, не маскировать постоянный OOM swap-файлом.

#### Сборка и доверие артефакту

CI по main проверяет **конкретный commit**, собирает image с locked dependencies, выполняет
`collectstatic` и `compilemessages` с отдельными build settings без подключения к production БД.
Фиктивный build SECRET_KEY не попадает в runtime fallback: production settings требуют настоящий.
В image нет `.env`, Git checkout, дампов, npm dev-dependencies и тестовых credentials. `.dockerignore`
явно исключает их, а содержимое image проверяется.

Публиковать `ghcr.io/sobolevbel/lexinform-web@sha256:…`; package сделать публичным отдельной настройкой:
публичность GitHub-репозитория сама по себе её не гарантирует. Build SHA, lock hash и версия схемы
payload записываются в OCI labels/release manifest. После push smoke запускается из опубликованного
digest; подписать/attest средствами GitHub и проверить ожидаемый repository/workflow на deploy.
Digest защищает неизменяемость, но сам по себе не доказывает происхождение.

Предпочтительно один workflow: checks → image build → image smoke → deploy с `needs` и environment
production. PR выполняет проверки без deploy credentials. Если выбран `workflow_run`, проверить
успех, исходный репозиторий, событие push и main, использовать `head_sha`, не свежий `main` и не
артефакт недоверенного PR. `permissions` минимальны: checks contents:read; build packages:write и
attestation permissions только своей job; deploy — чтение metadata и SSH secret.

`concurrency: deploy-web-production`, `cancel-in-progress: false` + host lock. Старый workflow,
дошедший после нового, не должен откатить сайт: wrapper сверяет release ancestry/sequence;
понижение допускается только явной командой rollback. Зависимости Actions фиксировать полными SHA,
обновлять Dependabot. Миграция не исполняется в entrypoint каждого web-контейнера.

#### Последовательность обычного релиза

1. Взять host lock, проверить свободный диск, исходный active release и разрешённый digest.
   Pull/проверка образа и release manifest; ошибка оставляет текущий сайт работающим.
2. Проверить внешний свежий backup; сделать дополнительный `pg_dump -Fc` перед миграцией и
   сохранить его вне VPS. Deploy state записывается атомарно, содержит текущий/предыдущий digest,
   цвет, schema compatibility, static prefix, фазу операции и время.
3. Остановить новые служебные запуски, дождаться текущего импорта и drain worker с таймаутом.
   Если не завершился — отменить выкладку до миграции. Посетителей продолжает обслуживать web.
4. Одноразовым контейнером нового image выполнить `check --deploy`, migration plan и `migrate`
   с migrator credential, `lock_timeout` и отдельным `statement_timeout`. Миграция не делает сеть,
   перевод или неограниченный backfill. Сбой — старый web, восстановление расписания и сигнал.
5. Поднять неактивный цвет. Проверить readiness по loopback с правильным Host/forwarded scheme,
   прогреть главную, конкретное дело, гайд, поиск и static URL; проверить release SHA.
6. `caddy validate`, атомарно заменить **импортируемый существующей конфигурацией** upstream fragment,
   `caddy reload`; ошибка reload возвращает файл конфигурации, старый upstream остаётся активным.
   [Штатный reload Caddy](https://caddyserver.com/docs/command-line#caddy-reload).
7. С внешней CI job проверить HTTPS и ключевые URL, CSS/fonts, noindex preview и закрытую админку;
   на хосте — окно 2 минуты с проверкой ошибок. Неверный SHA, 5xx или сбой smoke возвращает upstream
   на старый цвет. Автоматически откатывать можно только в совместимом окне.
8. Запустить worker нового digest, выполнить безвредный task smoke, подтвердить heartbeat и
   восстановить timers. Ошибка worker — полный rollback web + worker либо явно помеченный
   неуспешный релиз с остановленной очередью; не сообщать зелёный результат только по HTTP 200.
9. Зафиксировать active release, оставить старый цвет тёплым на 10 минут, затем graceful stop.
   Старый image и static сохранять для отката. После удаления контейнера быстрый rollback включает
   время его старта; не обещать мгновенный switch.
10. Записать итог и длительности в журнал; уведомление о результате предусмотреть в существующий
    техканал при реализации. Полное падение хоста выявляет внешний monitor, а не cron этого хоста.

После перезагрузки VPS bootstrap unit читает durable release state и поднимает активный цвет,
БД, worker и timers; незавершённый deploy восстанавливается по фазе, а не по угадыванию имени
контейнера. Deploy-команда идемпотентна для того же digest. Отдельно проверить первый релиз,
у которого ещё нет previous image: до успешного smoke Caddy показывает страницу обслуживания.

#### Откат, миграции и обновления зависимостей

`deploy rollback` возвращает **web и worker** предыдущего совместимого digest; приостанавливает
timers, проверяет очередь и readiness, переключает proxy, затем возобновляет обработку. DB restore
автоматически не выполняется. Старые имена task-функций и формат их payload поддерживаются обоими
релизами; несовместимые задания остаются остановленными до согласованного исправления.

Миграции собственных приложений: expand → совместимые чтение/запись → backfill отдельной командой
→ переключение чтения → поздний contract. Удаление колонки допустимо только когда **самый старый
допустимый rollback image** её не использует, включая worker. При добавлении NOT NULL проверить
старые INSERT: Python default нового кода не помогает старому процессу; нужен подходящий
`db_default` или промежуточная nullable схема. Concurrent index — non-atomic migration и
обработка оставшегося invalid index при повторе. Линтер — часть контроля, не доказательство.
[Migration linter](https://github.com/3YOURMIND/django-migration-linter).

Обязательный compatibility job: поднять схему предыдущего релиза, применить новый migration plan,
запустить **старый image на новой схеме** и выполнить чтение, редакторскую запись и задачу.
Отдельно тестировать новый image с новой схемой. В тестовом env отключены внешние отправки.
Проверка должна включать migrations Wagtail/allauth/task backend: нельзя гарантировать их обратную
совместимость собственным линтером. Для несовместимого обновления использовать отдельное объявленное
maintenance window; не обещать zero downtime любой ценой.

#### Статика, медиа и состояние браузера при смене релиза

Каждый image содержит `collectstatic` с manifest storage; URL имеет release prefix
`/static/<build-sha>/…`. Deploy извлекает static из проверенного image в постоянный каталог,
Caddy раздаёт каталог read-only с immutable cache headers. Сохранять static текущего и двух
предыдущих релизов минимум 7 дней: вкладка со старым HTML должна загрузить старый CSS после switch.
WhiteNoise используется для локального/image smoke и как подготовка manifest/compression;
production static обслуживает Caddy. Не удалять все файлы `collectstatic --clear` из общего
каталога при каждой выкладке. [WhiteNoise](https://whitenoise.readthedocs.io/en/stable/django.html).

Media не входят в image и не меняются при rollback. Публичные изображения/renditions можно
раздавать через Caddy; закрытые Wagtail Documents — через проверку прав, не общий file server.
Оригиналы с приватным содержимым не хранить в публично доступном пути. Загружаемые SVG/HTML
запретить в A; размер и формат изображений проверять на сервере. Согласовать UID/GID writer и
reader без `chmod 777`. Восстановление backup охватывает и media, и БД.

Во всех цветах один SECRET_KEY и общие DB sessions. Proxy доверяется только на loopback,
Host allowlist/CSRF trusted origins ограничены реальными доменами. CSP для публичного сайта
может быть строгой сразу; Wagtail admin проверить отдельно в report-only и согласовать с его
widgets. Поддержка nonce в Django admin не гарантирует её во всех виджетах Wagtail. HSTS вводить
после проверки доменов, не включать preload автоматически.

### 25.11. Backup, наблюдаемость и аварийные действия

Backup A каждые 12 часов, внешнее шифрованное хранилище, политика 7 daily / 4 weekly / 3 monthly.
Начальный инструмент — restic с S3-compatible backend другого failure domain; конкретного
поставщика выбрать в WEB-05 по EU region, минимальному счёту, восстановлению и deletion protection.
Production web/worker не получают credentials удаления backup. Recovery key хранится вне VPS.

Обычный `pg_dump` даёт согласованный снимок БД, но не согласовывает его с файловой системой.
Для A backup wrapper кратко приостанавливает редакторские записи, media uploads, импорт и worker,
оставляя публичное чтение, затем делает dump и снимок media. После фиксации локального backup
набор отправляется во внешнее хранилище, записи возобновляются без ожидания долгой передачи.
При росте корпуса перейти на файловый snapshot/объектное versioning с согласованным manifest.
Не считать, что два параллельных `cp` и `pg_dump` решают эту задачу.

Backup manifest: UTC-время, active image digest, PostgreSQL major, migration head, media manifest,
контрольные суммы, source commit, номер поколения и инструкция восстановления secrets. Пароли
не печатать в manifest. `restic check` и успешный upload недостаточны: ежемесячно восстановить
на отдельную БД и media-path, выполнить migration/read smoke, проверить изображения, роли,
переводы, draft/live и предметные связи. [Восстановление restic](https://restic.readthedocs.io/en/stable/050_restore.html).

RPO A ≤24 часа, backup age warning после 18 часов, critical после 24. RTO ≤4 часов от начала
действий оператора проверяется учением «новый сервер → восстановленный сайт», включая secrets/DNS,
а не только `pg_restore`. В B уменьшить RPO до ≤6 часов; перед значимым объёмом аккаунтов оценить
WAL/PITR. Backup перед deploy не заменяет регулярный.

Health-контракт уточняет §24.8.8:

- `/healthz` — процесс способен ответить, без БД и внешних API; для liveness.
- `/readyz` — короткая проверка БД и поддерживаемой схемы данным image; 503 при неготовности.
  Проверку отсутствия неприменённых миграций выполнять при startup/deploy, результат хранить в
  процессе, не вычислять migration graph на каждом poll. Старый rollback image допускает новые
  совместимые миграции по release manifest.
- Privileged operations report — release SHA, импорт, источник/аспект, очередь, worker heartbeat,
  бюджет переводов, возраст backup; ошибки и секреты не выдаются анонимному health endpoint.
- Недоступный Sejm/RCL/GitHub/LLM ухудшает данные/очередь, но не превращает весь читаемый сайт
  в unready. Для этого отдельные freshness alerts и публичные предупреждения.

Structured logs: request_id, release_sha, status/duration, import_id, task_id, event_id. Не писать
полный URL с query/token, форму, email или содержимое prompt. Начальные alerts: >1% 5xx за
5 минут при достаточном числе запросов, нет worker heartbeat >5 минут, готовая задача ждёт >15 минут,
неудачные проверки импорта >20 минут, диск >80% / critical >90%, backup старше порога. Пороги
настраиваемые; повторные одинаковые уведомления подавлять, recovery сообщать один раз.

| Инцидент | Действие runbook |
| --- | --- |
| Ошибка web после выкладки | rollback совместимого image, проверить публичный smoke; сохранить логи/manifest |
| Новая схема state не читается | Оставить последнее поколение, обновить адаптер; не принимать неполный снимок |
| Неверные новые факты | Остановить импорт, посмотреть diff; возврат active generation с аудитом, без отката редакторских записей |
| Worker упал во время перевода | Проверить lease/request ID, восстановить заявку; не повторять весь backfill |
| Диск заполнен | Остановить тяжёлые jobs, убрать только известные временные файлы/старые логи; не удалять media/БД |
| VPS потерян | Bootstrap нового хоста, restore БД/media/config, проверка digest, переключение DNS |
| Ошибка backup | Сайт продолжает читать; устранить до окна RPO, deploy блокируется при отсутствии пригодной копии |

Staging по требованию — отдельные DB credentials, volumes, Compose project и домен за авторизацией.
Синтетические данные, disabled sends и fake translation provider; никаких production bot/API keys.
На 4 GB не держать staging вместе с blue/green без замера: локальный Compose/CI PostgreSQL являются
основной средой, дополнительный VPS оплачивается по времени только при необходимости.

### 25.12. Среда разработки и команды, которые экономят время

Обычный цикл: Python/uv на хосте для быстрого autoreload, PostgreSQL в Compose. Перед merge —
полный container smoke того же image, что будет на VPS. На Apple Silicon локальная БД может быть
arm64, production x86_64 проверяет Linux CI; не объявлять Mac достаточной проверкой wheels.

Один `justfile` — короткие входы в команды, реальная логика — типизированные management commands
и небольшие проверяемые shell scripts. У `just --list` есть описания; ни одна команда локальной
разработки не получает production доступ по умолчанию. Скрипты не зависят от RTK: это личная
обёртка команды, не runtime-зависимость сайта или CI.

| Команда, которую реализуем | Поведение |
| --- | --- |
| `just bootstrap` | Проверяет uv/Python/Docker/Node для e2e, синхронизирует lock, создаёт только отсутствующий локальный env, запускает БД, migrate, seed |
| `just dev` | Django autoreload + локальная БД, fake LLM, console mailer; worker запускается отдельной командой |
| `just worker` | Worker local settings с reload, видимые имена очередей и лимиты |
| `just manage <args>` | Короткая обёртка `uv run --package lexinform-web python web/manage.py …` |
| `just seed` | Идемпотентно создаёт пять локалей, дерево CMS, группы и синтетические сценарии; без фиксированного production пароля |
| `just doctor` | Проверяет версии, DB extensions, schema head, конфигурацию очереди, locales, writable paths; секреты только present/missing |
| `just check` | Полный gate бота + web: типы, lint/format, tests, migrations, templates, translations |
| `just test-web` / `just e2e` | PostgreSQL-сценарии / Playwright; отдельные короткие циклы |
| `just import-fixture` | Два фиксированных снимка подряд, демонстрация обновления и сохранения правки |
| `just inspect-state <sha>` | Read-only fetch в временную директорию, отчёт о схеме/связях/кандидатах; не запускает бота |
| `just translation-plan` | Число документов/локалей, оценка токенов, лимит; без платного API |
| `just translations-fake` | Полный цикл перевода с детерминированным fake, включая stale/conflict |
| `just import-guides --dry-run` | Показывает Markdown → blocks, не публикует; применение создаёт drafts по стабильным ключам |
| `just export-content` | JSON/Markdown опубликованных материалов и metadata; без пользователей/секретов |
| `just image-smoke` | Сборка и запуск production image с тестовой БД, migrate, static/health/страницы |
| `just deploy-status` | Read-only release/schema/import/backup summary по явно заданному окружению |
| `just rollback <environment>` | Явная операционная команда, показывает current/target digest и compatibility |
| `just restore-test <snapshot>` | Новые отдельные DB/media, disabled sends, smoke, отчёт RTO |
| `just reset-local` | Только известная local DB/volume, проверка имени и окружения, явный destructive флаг |

`.env.web.example` содержит только настройки сайта: `DATABASE_URL`, `SECRET_KEY`, `ALLOWED_HOSTS`,
`PUBLIC_BASE_URL`, `CSRF_TRUSTED_ORIGINS`, media/static paths, read-only state source, task limits,
translation model/budget, mailer mode, log level. Разделять local/test/production, production
не имеет скрытых default для секретов. Чтение локального env явно привязано к web-файлу, не к
первому `.env`, найденному выше по дереву. При добавлении общих settings обновить `.env.example`
и README согласно правилам проекта. В тестах production settings собираются на фиктивных значениях.

`seed_demo` использует фиксированную дату через Clock. Корпус: Sejm consultation, RCL с письмом,
Wykaz без текста, RPW → druk, joint prints, rejected, veto pending, awaiting promulgation,
published/partially in force, irrelevant, source outage, missing translation, protected override,
guide due for review. Тестовые IDs и тексты явно синтетические, не выглядят правовым советом о
действующем законе. UI-галерея `/__components__/` доступна только local/test и показывает состояния
на пяти языках с переключаемой датой.

У каждой management command: `--dry-run`, если она изменяет предметные данные; JSON summary для
автоматизации; понятные exit codes; ограниченный размер batch; resume cursor для долгого backfill.
Не прятать бизнес-логику в justfile, bash или Django signals. Signals допустимы для уведомления
о публикации, но надёжность обеспечивается сохранённой записью и reconciliation.

Первое добавление web обновляет CONTRIBUTING: «с чистого clone до страницы без API keys за
10–15 минут», отдельно обычный dev, тесты и production runbooks. Команды проверяются в CI с
пустой БД, а не существуют только как удачный набор в терминале одного разработчика.
