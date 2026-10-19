# Итоговые отчёты экспериментов

**Программа завершена: 1512/1512 основных запусков — 19 октября 2026 года, 16:44 МСК.** Это 576 натурных измерений, 576 BrokerFidelity/holdout и 360 сетевых симуляций. Pilots, технические повторы и диагностика считаются отдельно.

Начните с [микроотчёта о выборе брокера](Best-broker.md), затем [сравнения по диссертации и статьям](Broker-comparison.md) и [проверки MqttY/MqttRelay](Model-validation.md).

| Брокер | HTML | Натурные | Модели | ⚠ Fidelity / контроль | EST\* |
| --- | --- | --- | --- | --- | --- |
| [Aedes](Aedes.md) | [HTML](Aedes.html) | 144/144 | 216/216 | 8 / 14 | 0.5520 |
| [Mosquitto](Mosquitto.md) | [HTML](Mosquitto.html) | 144/144 | 216/216 | 2 / 14 | 0.5623 |
| [ActiveMQ Classic](ActiveMQ.md) | [HTML](ActiveMQ.html) | 144/144 | 216/216 | 2 / 43 | 0.2459 |
| [EMQX](EMQX.md) | [HTML](EMQX.html) | 144/144 | 216/216 | 0 / 14 | 0.4505 |

[Смешанная сеть MqttRelay: 72/72](MqttRelay-Mixed.md) · [HTML](MqttRelay-Mixed.html). Четыре неуспешные натурные попытки были повторены; отдельно сохранена прерванная попытка Mosquitto. Предыдущие незавершённые когорты не добавлены к текущему покрытию.

**Полнота программы не означает точность модели.** Ни один двойник не прошёл все независимые сценарии. Повторены шесть выбранных моделей: научные итоги совпали точно. Натурная точность Relay-сети этой кампанией не установлена.

[Отдельная оценка достоверности, верификации метрик, воспроизводимости и EST](Evidence-assessment.md) · [HTML](Evidence-assessment.html). В документе приведены независимый арифметический пересчёт и границы доказательств.

## Данные и проверяемость

[Снимок источников](data/snapshot.json) сохраняет исходные identities и SHA-256. [Финальный state](data/state.json) фиксирует завершение. [Реестр raw-архивов](data/archive-registry.json) указывает материалы на hd250. Полные raw не копируются в Git. [Хэши опубликованных файлов](publication-sha256.json) позволяют проверить неизменность публикации.

Архивы хранятся **только на hd250, ZIP в Git не включаются**. [Реестр внешних архивов: пути, размеры и SHA-256](data/external-archives.json). В native-members.zip сохранены sealed результаты, телеметрия, guard и версии всех 576 успешных натурных измерений; в пяти *-model-evidence.zip — исходные конфигурации, manifests, counters, summaries, распределения и графы 936 моделей. В репозитории находятся их компактные JSON-сводки. Сводки: [натурные](data/native-evidence.json), [модельные](data/model-evidence.json), [повторяемость](data/replay-results.json), [EST](data/integral-assessment.json).

При финальном сборе сверены seals прочитанных членов архивов, средние и CI95, все модельные counters и критерии, неизменность профилей. Квитанции прежнего восстановления raw и повторной агрегации сохранены отдельно у каждой серии; сбор публикации не является новым полным пересчётом всех ID. [Отдельный аудит 24 raw ActiveMQ](ActiveMQ-diagnosis.md).

HTML содержит графики внутри файла и работает без интернета. Для ссылок на CSV/JSON и другие документы нужен остальной каталог проекта. Архивы моделей — данные; при повторении заменяются только пути к источникам с сохранением хэшей профилей и версии runtime.

## Воспроизвести публикацию

```bash
python -m pip install -r tools/campaign/report-requirements.txt
python tools/campaign/render-final-reports.py --output reports
python -m unittest discover -s tools/campaign -p test_final_reports.py -v
```

Генератор проверяет опубликованные JSON и пересчитывает таблицы, графики, оценки и HTML без ZIP. Для дополнительного чтения исходных модельных файлов и проверки архивов передайте `--archive-root <каталог-внешних-архивов>`. Для сбора нового завершённого снимка: collect-final-evidence.py; для выбранных повторений: replay-final-models.py. [Описание инструментов](../tools/campaign/README.md).

## История

[Полная и сокращённая программы](Full-vs-Compact.md) · [Mosquitto](history/mosquitto-fidelity-diagnosis.md) · [Эмпирическая модель](history/empirical-fidelity-deployment.md) · [Исправление времени](history/empirical-service-budget-fix.md) · [Ночная серия](history/overnight-campaign-20261016.md) · [Развёртывание модели](history/service-budget-linux-deployment.md) · [Телеметрия](history/instrumentation-repair-20261018.md). Исторические документы описывают свои даты, а не текущее состояние.

[Протокол проверки итоговой публикации](data/publication-validation.json): 11 тестов, проверка восьми HTML и побайтовое совпадение результатов пересчёта с проверкой внешних архивов и без неё.
