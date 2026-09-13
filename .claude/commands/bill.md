---
description: Разбор одного законопроекта — что о нём знают API и база, что увидел читатель и что сделает следующий прогон
argument-hint: <номер: 2799 | RPW/29075/2026 | RCL/12412103 | WPL/UD408>
---

Разбери законопроект `$1` на копии продового состояния: только чтение, ничего не постить,
ничего не менять в проде.

## Подготовка

```bash
git fetch -q origin state
git show origin/state:lexinform.sql > /tmp/lexinform-state.sql
export LEXINFORM_DB_PATH=/tmp/lexinform-bill.db
rm -f "$LEXINFORM_DB_PATH"
uv run lexinform db init && uv run lexinform db restore /tmp/lexinform-state.sql
```

## Что собрать

```bash
uv run lexinform show $1        # API (или RCL/wykaz) + стадии + вложения + строка из базы
uv run lexinform preview $1     # карточка, как её увидит читатель (если есть анализ)
```

и строки базы (`sqlite3 "$LEXINFORM_DB_PATH"`):

- `bills`: `status`, `prefilter_hits`, `analysis_attempts`, `last_error`, `discontinued_at`,
  `stages_fingerprint`, `linked_wykaz_number`, `supplements_json`, `first_seen_at`;
- `status_changes` по этому номеру: что уже считалось новостью и когда;
- `publications` по этому номеру: `kind`, `status`, `message_id`, `error` — есть ли `pending`,
  `failed`, `unknown`, не задвоена ли карточка, есть ли `joint_bill` вместо `new_bill`.

Если у законопроекта есть связанная строка (RPW → друк, RCL → друк, WPL → RCL), собери и её:
тред один, и вопрос «почему читатель видит это» часто отвечается соседней строкой.

## Что объяснить

1. **Где он сейчас**: последняя стадия по `models.process_stages` (не последний узел дерева —
   `End` «Uchwalono» появляется на третьем чтении и ничего не говорит о том, где бильд), что
   об этом говорит `next_phase`, и не считает ли `is_over` его законченным.
2. **Почему у него такой статус**: прошёл ли префильтр по заголовку и по тексту, был ли анализ,
   почему пропущен (`skipped_cost`, `skipped_closed`, `skipped_prefilter`,
   `skipped_text_prefilter`, `analysis_failed` с `last_error`).
3. **Что увидел читатель**: карточка и все реплаи под ней по `publications`; совпадает ли
   «что дальше» и «что можно сделать сейчас» с реальной стадией и сроками на сегодня.
4. **Что сделает следующий прогон**: будет ли анализ, апдейт, перерисовка карточки
   (`rendered_sha256` дрейфует), напоминание о сроке, ничего.
5. Если что-то не так — какая ровно команда это чинит (`lexinform reset --to …`,
   `lexinform republish`, операторская команда из `docs/operator-commands.md`) и почему именно она.

Расхождение между тем, что говорит карточка, и тем, что означает поле API, описывай как
находку с конкретной строкой кода — правки вноси только если попросили.
