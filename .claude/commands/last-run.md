---
description: Разбор последних прогонов — сходятся ли счётчики отчёта, что попало в канал и что потерялось навсегда
argument-hint: [сколько прогонов, по умолчанию 2] [номера карточек, если их надо сверить с каналом]
---

Разбери последние прогоны на копии продового состояния: только чтение, ничего не постить,
`lexinform run` против этой базы не запускать. Сколько прогонов брать — из `$ARGUMENTS`, по
умолчанию два последних.

## Подготовка

```bash
git fetch -q origin state
git log --oneline -3 origin/state                  # когда дамп писался последний раз
git show origin/state:lexinform.sql > /tmp/lexinform-state.sql
export LEXINFORM_DB_PATH=/tmp/lexinform-runs.db
rm -f "$LEXINFORM_DB_PATH"
uv run lexinform db init && uv run lexinform db restore /tmp/lexinform-state.sql
sqlite3 "$LEXINFORM_DB_PATH" "select id, started_at, finished_at, since, mode, ok from runs order by started_at desc limit 5"
sqlite3 "$LEXINFORM_DB_PATH" "select report_json from runs where id = <ID>" | python3 -m json.tool
```

Дамп пишется в конце прогона, так что последний коммит `state` — это и есть последний разобранный
прогон. Если в канале есть отчёт, которого нет в `runs`, прогон упал до `db dump` — это первая
находка.

## 1. Сходятся ли счётчики отчёта с базой

Отчёт — это агрегат; база помнит, что с каждой строкой на самом деле стало. Расхождение здесь
почти всегда означает потерянный законопроект.

```sql
-- что прогон создал (окно = started_at..finished_at прогона)
select number, status, substr(coalesce(last_error,''),1,70) err,
       json_extract(analysis_json,'$.analysis.score') score, substr(title,1,45)
from bills where first_seen_at >= '<started_at>' order by first_seen_at;
```

- `discovered` / `rcl_discovered` / `wykaz_discovered` = число созданных строк своего вида;
- `text_prefilter_checked` = `hits` + `scans` + `unreadable` + `unanswered` + `failed` + промахи
  по ключевым словам; каждая единица в `unreadable`, `unanswered`, `failed` должна находиться
  строкой с `last_error` — найди её поимённо, счётчик сам по себе ничего не значит;
- `analyzed` + `triaged_out` + `analysis_failures` + `analysis_skipped_cost` +
  `analysis_unanswered` = сколько кандидатов вошло в фазу анализа (`hits` + `scans` + попадания
  по заголовку);
- `published` + `joint_published` = сколько кандидатов прошло `min_score`; проверь обратное —
  нет ли релевантного анализа со `score >= min_score` без `sent`-карточки:

```sql
select number, json_extract(analysis_json,'$.analysis.score') sc, status from bills
where analysis_json is not null and sc >= 3
  and json_extract(analysis_json,'$.analysis.relevant') = 1
  and not exists (select 1 from publications p where p.term = bills.term
                  and p.number = bills.number and p.kind in ('new_bill','joint_bill')
                  and p.status = 'sent');
```

- сумма `llm_calls` должна объяснять строку «≈ $N» и укладываться в `LEXINFORM_MAX_RUN_COST_USD`,
  а каждая отдельная первая аналитика — в `LEXINFORM_MAX_ANALYSIS_COST_USD`.

## 2. Скипы, которые не оживут сами

`skipped_text_prefilter` пересматривается только при смене заголовка (`discovery._ingest`) или
вручную (`lexinform reprefilter --include-text-skipped`, `/unskip`). Поэтому разбирай причину
каждого скипа, а не только их число:

```sql
select number, status, last_error, last_checked_at from bills
where last_error like '%text prefilter%' order by last_checked_at desc limit 20;
```

- `no document to read` — это **не** решение о законопроекте: у друка ещё не выложили PDF
  (Сейм регистрирует процесс раньше файла — проверь `changeDate` у `/sejm/term10/prints/N`:
  если он позже прогона, файл появился после нас), либо у проекта RCL ещё пустые каталоги.
  Строка при этом закрыта навсегда — называй её находкой и предлагай `reprefilter`/`/unskip`;
- `no text layer and no pages` / `file over the download size limit` — файл действительно нечитаем;
- `weak patterns only` / `under the threshold` — так задумано, но посмотри на числа: сильный
  паттерн с большим счётом рядом со слабым стоит показать оператору;
- `unanswered` (WAF orka) — строка осталась `text_prefilter_pending`, это норма, следующий
  прогон спросит снова; тревога, если она висит так несколько прогонов подряд.

## 3. Что увидел читатель

Для каждой карточки и реплая прогона:

```bash
uv run lexinform show <номер>      # источник: стадии, вложения, строка базы
uv run lexinform preview <номер>   # карточка ровно так, как её рендерит прод
```

- сверь `preview` с тем, что в канале (если текст канала дали в аргументах): расхождение значит,
  что карточка дрейфанула, а `CardRefresher` её не переписал;
- **стадия против источника**: для RCL прочитай живую страницу тем же парсером и сравни
  `current_stage` и `status` со `stages_json`; для Сейма — `process_stages` (последняя
  содержательная стадия, не последний узел: `End` «Uchwalono» появляется на третьем чтении);
- **«что дальше»**: фаза из `next_phase`, дата — из `bill.agenda` или конституционного срока;
  просроченная дата не печатается, шаг со своей датой не бывает «без движения»;
- **«что можно сделать сейчас»** — самое частое место ошибки. Проверь, что действие
  соответствует открытому окну: у проекта RCL это `bill.consultation` (`_rcl_actions`), и если
  окно неизвестно, карточка предлагает комментарий и zgłoszenie с интонацией открытых
  консультаций — см. проверку ниже;
- ссылки в карточке: все ли живые (`curl -s -o /dev/null -w '%{http_code}'`), особенно форма
  комментария RCL и адрес министерства.

## 4. Окно консультаций у проектов, найденных по тексту

Проект, взятый по тексту (`🔎 Найден по тексту проекта`), проходит через
`RclProjectReader.with_text` — один каталог, без письма. `complete()`, которое разбирает письмо
(срок + e-mail), зовут только для попадания по заголовку, из `lookup` и из линкера wykaz. Ищи
дырку так:

```sql
select number, status, json_extract(rcl_json,'$.consultation') cons from bills
where rcl_json is not null and status = 'analyzed' and cons is null;
```

Для каждой такой строки прочитай каталог стадии «Konsultacje publiczne» и посчитай, что сказало бы
письмо (`container.rcl_reader().consultation(project)` в одноразовом скрипте в скретчпаде). Если
срок и адрес есть на RCL, а в базе `null` — карточка вышла без срока, без e-mail, без напоминания
о консультации, и (когда срок уже истёк) без фразы «окно закрыто»: `action_rcl_window_closed`
не может сработать, пока `window is None`.

## 5. Публикации и очередь

```sql
select kind, status, count(*) from publications group by 1, 2;
select id, kind, number, status, attempts, error from publications where status != 'sent';
select p.number, b.status, b.last_error from publications p join bills b using (term, number)
where p.kind = 'new_bill' and p.status = 'sent' and b.status not in ('analyzed','linked');
```

`pending` от упавшего прогона сам не дошлётся; `failed` ретраится, но проверь `attempts`;
карточка у строки со статусом `skipped_*` законна только если это `/skip` оператора
(`last_error` скажет). Ещё посмотри `cards_refreshed`: каждая единица — отредактированное
сообщение в канале, и после смены форматтера их бывает много — это ожидаемо, но скажи об этом.

## 6. Расписание и время фаз

`gh run list --workflow daily.yml -L 10` — будни 05:23 и 16:23 UTC, выходные 10:23, GitHub
стартует на 3–4.5 часа позже. `phase_seconds`: RCL-дискавери в сотни секунд — норма (страница
~10 с), а вот текстовый префильтр в минуты означает сканы или большие PDF; сравни с прошлыми
прогонами через `uv run lexinform runs --days 14`.

## Отчёт

Сначала два-три предложения: что прогоны сделали и что из этого увидел читатель. Дальше — находки
по убыванию важности, каждая с конкретной строкой (номер законопроекта, поле, файл:строка кода) и
с тем, чем чинится: командой оператора, `lexinform reset --to …`, `reprefilter`, или правкой в
коде. Отдельно назови то, что потеряно **навсегда**, если ничего не делать. Если всё чисто — одна
фраза, без пересказа счётчиков. Правки в код вноси только если попросили.
