# MqttY из develop: Windows x64 → ALT Linux / Эльбрус

**PASS научной воспроизводимости: 6/6 сценариев, 12/12 удалённых запусков.**
Это повторный эксперимент из версий, закреплённых в `develop` метарепозитория.
Он заменяет прежний отчёт и исходные артефакты по этому пути. Старые версии
сохранены в истории Git; результаты разных сборок не смешивались.

Два отдельных Linux-процесса каждого сценария совпали друг с другом и с новым
Windows baseline по всем научным fingerprints. Единственное различие — поле
runtime: `.NET 6.0.10` на Linux против `.NET 6.0.36` на Windows.

## Исходники, сборка и выполнение

| Параметр | Значение |
| --- | --- |
| Ветка метарепозитория | `develop` |
| Снимок метарепозитория при сборке | `487b23c9269229a54246cf0013e9df40817ebbd1` |
| MqttY из gitlink develop | `f5c0a4715b555c4348d2a2843177251f62c640ff` |
| MqttBenchmark из gitlink develop | `ab513df3d2b65462abcfba8084f066ae4b1bd0a1` |
| Сборка | Windows x64, SDK 9.0.202, Release, `net6.0` |
| Публикация | Управляемые DLL без RID и apphost; `--self-contained false -p:UseAppHost=false` |
| Windows baseline | Заново выполнены 12 запусков этой сборки, все пары совпали |
| Целевая машина | `user@10.100.101.14`, ALT Workstation 11.2, e2k |
| Ядро / RID | Linux 6.1.128-elbrus-def-alt1.2.3 / `elbrus.8.0-e2k-v4` |
| Runtime / Python | .NET 6.0.10 / Python 3.12.13 |
| Сборка на Linux | Не выполнялась: `buildOnThisMachine=false` |
| Принятая Linux-серия | 02.11.2026, **20:58:15–21:03:17**, Europe/Moscow |
| Интервал серии | Около 5 мин 2 с по первому run ID и последнему manifest |
| Код завершения runner | 0 |
| Удалённый каталог | `/home/user/mqtty-develop-20261102-4801633c` |

Для сборки использованы отдельные чистые копии **точных gitlink-версий** root
`develop`; это не подразумевает произвольные более поздние вершины веток подмодулей.
Пакет и все его 31 входящий файл проверены по SHA-256 перед запуском.

Первый фоновый запуск не пережил закрытия SSH и не завершил ни одной симуляции.
Его файлы сохранены на сервере с суффиксом `.interrupted-001`. Принятая серия
перезапущена с удержанием SSH-соединения и новым каталогом `results`. В статистику
включены только 12 полностью завершённых запусков этой серии.

## Конфигурация

Один моделируемый брокер, один издатель и один подписчик; `brokerFidelity`,
`profileClientCountMode=connectedClients` (2 подключения). Два режима — ideal
и legacy Mosquitto v2. Payload 16 байт, seed 42, publish QoS равен subscribe QoS,
QoS 0/1/2, `parallel=false`, `refreshStrategy=0`.

Тик 0,1 мс; прогрев 1000 тиков (0,1 с), измерение 10000 тиков (1 с), drain до
20000 тиков (2 с) виртуального времени. Offered RPS 1000; ёмкость сети
100 000 000 байт/с, очередь 100 000, transferTicks=1, outgoingWaitTicks=1.
Каждый сценарий выполнен дважды в отдельном процессе.

Реальные MQTT-брокеры, Docker и сетевые измерения не использовались.

## Результаты

Один повтор показан в таблице; второй совпал. P99 округлён только в таблице;
исходные значения JSON и fingerprints сравнивались без округления и допусков.

| Модель | QoS | Attempted | Completed | Publish failed | Delivered | P99, мс | Windows baseline |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Ideal | 0 | 1000 | 1000 | 0 | 1000 | Не применимо | Совпадает |
| Ideal | 1 | 1000 | 1000 | 0 | 1000 | 0,6 | Совпадает |
| Ideal | 2 | 1000 | 1000 | 0 | 1000 | 1,2 | Совпадает |
| Legacy Mosquitto v2 | 0 | 1000 | 951 | 49 | 951 | Не применимо | Совпадает |
| Legacy Mosquitto v2 | 1 | 1000 | 1000 | 0 | 1000 | 1,4 | Совпадает |
| Legacy Mosquitto v2 | 2 | 1000 | 1000 | 0 | 1000 | 2,5 | Совпадает |

Во всех сценариях `rateRejected=0`, `deliveryDropped=0`, `deliveryLossRate=0`.
Для legacy QoS0 publish failure rate равен **0,049**. Во всех шести сценариях
attempted равен ровно 1000 за измерительное окно. Ранее наблюдавшееся значение
1001 в старой сборке в этой серии не воспроизводится; отдельное доказательство
причины изменения в рамках данного эксперимента не выполнялось.

В машинном отчёте:

- `allLocalRepeatsMatch=true`;
- `allScientificFingerprintsMatch=true`;
- `allRuntimesMatch=false`;
- в каждом `differentFields` присутствует только `runtime`.

Проверены полная summary, брокерные counters, полная latency histogram, границы
фаз, число подключений и конфигурация. Timestamps, run IDs, ОС, CPU, строковый
общий dump counters и формат файлов топологии не являются критериями равенства,
но сохранены в исходных артефактах.

## Fidelity и ограничения

У ideal-запусков CLI-код 0; у legacy-запусков — 3. Reference RPS не пройден
при всех трёх QoS; reference p99 дополнительно не пройден при QoS1/2. Эти статусы
сохранены, а не заменены успехом replay. Профиль помечен `provisional-legacy-v1`;
точность относительно реального Mosquitto не доказана.

Не проверены payload 256, seeds 43/44, другие нагрузки, масштабные и relay-топологии,
реальные брокеры и производительность физического оборудования. Два повтора
на сценарий подтверждают совпадение этих запусков, а не детерминизм всех моделей.
Побайтовая воспроизводимость пересборки бинарников и offline NuGet restore
не обеспечиваются этим экспериментом.

## Проверяемые артефакты

Скачан архив результатов; его SHA-256 проверен после передачи. Дополнительно
проверены все **102 файла** реестра. Исходные bundles, конфигурации и логи полностью
заменены новой серией; Git-конверсия окончаний строк для `evidence/` отключена.

| Объект | SHA-256 |
| --- | --- |
| Профиль, точные LF-байты | `b09938696ead7c7a77548fd4b3fe2134a20c303cebfd3aced592186bd2e6ab6a` |
| DLL приложения | `bc8757a1b7e065566603c94aa26ca1e9379a194bb4a96aa1fc9951e9bac63605` |
| Пакет доставки | `cded1491c0800a19347b8d84aae59eae7e154546aa0f3f63372045876e5fb816` |
| Архив результатов | `a96b165e64b61b5a5bbb2cb5e69c6e9c23e83966cda0319bd5559837735c6829` |

- [Итоговое сравнение](evidence/results/comparison.json).
- [Окружение Linux](evidence/results/environment.json), [окружение Windows baseline](evidence/inputs/windows-baseline-environment.json).
- [Лог серии](evidence/run.log), [код завершения](evidence/exit-code.txt).
- [Реестр SHA-256](evidence/result-export-sha256.json), [manifest пакета](evidence/package-manifest.json).
- [Снимок исходников develop](evidence/build-source.json).
- [Новый Windows baseline](evidence/inputs/baseline-windows-smoke.json), [профиль](evidence/inputs/broker-benchmark.v2.json).
- Точные скрипты запуска: [run.py](evidence/inputs/run.py), [run_prebuilt.py](evidence/inputs/run_prebuilt.py).
- Исходные данные: `evidence/results/<scenario>/repeat-<1|2>/<run-id>/`.

Проверка без SSH, .NET и пересборки, из корня репозитория:

```bash
python3 reproducibility/verify_evidence.py reproducibility/reports/linux-e2k-20261102
```

**Готовность:** ограниченный smoke-набор из закреплённых версий `develop`
воспроизводится на Windows x64 и ALT Linux/e2k с различными версиями runtime.
