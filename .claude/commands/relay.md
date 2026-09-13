---
description: Проверить реле на VPS, очередь операторских команд и последние прогоны GitHub Actions
argument-hint: [--logs N]
---

Проверь, жив ли контур операторских команд: реле на mikrus → ветка `inbox` → запуск в Actions.
Только чтение: ничего не рестартить, не деплоить и не удалять файлы из очереди без прямой
просьбы. Аргументы: $ARGUMENTS

## Что посмотреть

```bash
gh run list --workflow ci.yml -L 5
gh run list --workflow deploy-relay.yml -L 5     # деплой реле идёт после зелёного CI на main
gh run list --workflow daily.yml -L 5

ssh mikrus 'systemctl is-active lexinform-listen; systemctl show -p ActiveEnterTimestamp -p NRestarts lexinform-listen'
ssh mikrus 'journalctl -u lexinform-listen -n 50 --no-pager'
ssh mikrus 'git -C /opt/lexinform rev-parse --short HEAD'   # сравни с origin/main
git fetch -q origin main && git rev-parse --short origin/main

git fetch -q origin inbox
git ls-tree -r --name-only origin/inbox   # файлы команд, ожидающие прогона
```

## Что из этого следует

- Реле — это только `lexinform listen` (один потребитель `getUpdates` на бота, никаких
  вебхуков): 384 МБ на VPS хватает на цикл опроса и не хватает на сам бот. Падения и рестарты
  в `NRestarts` — сигнал, что что-то не так с сетью или токеном, а не с базой.
- Ветка `inbox` должна быть почти всегда пустой: файл `{update_id}.json` живёт от записи реле
  до конца фазы `commands` ближайшего прогона. Залежавшийся файл значит, что
  `repository_dispatch` потерялся (тогда его подберёт следующий плановый прогон) или фаза
  падала — посмотри в базе таблицу `commands`: записана ли строка и есть ли `executed_at`.
  Команда, записанная но не отвеченная, повторно **не выполняется** — оператору отвечают тем,
  что знает строка.
- Коммит на VPS отстаёт от `origin/main`, а `deploy-relay.yml` зелёный → деплой прошёл, но
  сервис мог не перезапуститься; красный → смотри лог этого запуска, деплой идёт одной SSH
  командой, привязанной к `deploy/update.sh`.
- Пуш в `main` деплоит всё: дневной прогон берёт `main` на каждом запуске, реле обновляется
  после зелёного CI. Незелёный CI на `main` — это не только «тесты упали», но и «реле осталось
  на старом коде».

## Отчёт

Три строки состояния (реле, очередь, Actions) и список того, что требует вмешательства, с
конкретной командой на каждый пункт. Если всё в порядке — так и скажи.
