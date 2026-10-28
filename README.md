# Draft Helper — подсказчик героев для Dota 2

Во время драфта окно поверх игры показывает топ-5 героев для выбора и объясняет, почему именно они: насколько хорошо герой играет против уже выбранных врагов и с напарником по линии.

**[Скачать Draft Helper.exe](https://github.com/zurcsed/dota-draft-stats/raw/main/Draft%20Helper.exe)** (Windows, один файл, установка не нужна)

## Как пользоваться

1. Скачайте и запустите `Draft Helper.exe`.
2. При первом запуске программа предложит подключиться к Dota 2 — согласитесь и перезапустите игру.
3. В настройках Dota 2 включите режим «Окно без рамки», иначе окно подсказки не будет видно поверх игры.
4. На драфте окно само покажет подсказки. Если героя не удалось распознать с экрана, его можно выбрать вручную.

Программа использует только публичную статистику и официальные данные игры (Game State Integration). Она не читает память игры.

## Откуда статистика

- **Общая база** — `stats.json` на GitHub Pages. GitHub Actions пересобирает её каждые 6 часов из STRATZ API (все ранги, 4 завершённые недели и текущая). Программа качает файл сама, свой ключ STRATZ не нужен.
- **Матчи Valve** — отдельный сервер собирает публичные матчи через Steam Web API, считает по ним винрейты и контрпики и отдаёт файл `valve_stats.json`. Ранги, позиции и линии Valve не отдаёт, они берутся из STRATZ.

## Что в репозитории

| Файл | Назначение |
| --- | --- |
| `build_feed.py` | собирает `stats.json` для GitHub Pages |
| `data.py`, `stratz.py`, `paths.py` | загрузка и разбор статистики (общие с программой) |
| `valve_collect.py`, `valve_export.py`, `valve_stats.py` | сбор матчей Valve и расчёт статистики |
| `server/` | установка сборщика на сервер (Ubuntu, systemd) |
| `.github/workflows/feed.yml` | сборка и публикация `stats.json` |

Исходный код самой программы (окно, распознавание экрана) здесь не лежит, в репозитории только её сборка.

## Настройка (для владельца репозитория)

- Секрет репозитория `STRATZ_TOKEN` — токен STRATZ API (stratz.com/api).
- Переменная репозитория `VALVE_STATS_URL` — адрес `valve_stats.json` с сервера сбора. Если пусто, берутся числа STRATZ.
- Settings → Pages → Source: «GitHub Actions».
- Ручной запуск: Actions → «Update hero stats» → «Run workflow».
- Сервер сбора: `bash server/setup.sh` от root, ключ Steam Web API кладётся в `/etc/dota-collector.env`.

---

# English

Draft Helper shows an overlay with the top-5 hero picks during a Dota 2 draft and explains each one (matchups against picked enemies, lane partner synergy).

**[Download Draft Helper.exe](https://github.com/zurcsed/dota-draft-stats/raw/main/Draft%20Helper.exe)** (Windows, single file, no installation).

This repository publishes the shared statistics for the app: a GitHub Actions job builds `stats.json` from the STRATZ API every 6 hours and serves it on GitHub Pages. A small server collects public Valve matches (Steam Web API) and provides win rates and counters computed from them. Only public statistics and Valve's official Game State Integration are used; game memory is never read.

Setup: repository secret `STRATZ_TOKEN`, optional variable `VALVE_STATS_URL`, Settings → Pages → Source: "GitHub Actions".

## License

[MIT](LICENSE)
