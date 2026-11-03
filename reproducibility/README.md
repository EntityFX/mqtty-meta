# Воспроизводимый MqttY: develop, Windows → Linux

## Бенчмарк трёх сетей: serial / parallel, 03.11.2026

Локально выполнены **30 учётных попыток**, по пять на каждую пару сеть/режим.
Время — медиана завершённых процессов в секундах, включая .NET/JIT и запись результата.
Пилоты, сборка, передача и проверка файлов в эти значения не входят.

| Сеть | Последовательно, с | Параллельно, с | Завершено parallel | Научные данные serial/parallel |
| --- | ---: | ---: | ---: | --- |
| relay | 5,810 | 5,852 | 5/5 | Не совпали; время parallel диагностическое |
| federation | 4,701 | 4,424 | 4/5 | Не совпали; одна ошибка Refresh; время parallel диагностическое |
| load-balancer | 3,480 | 3,486 | 5/5 | Совпали во всех пяти повторах; ускорения не наблюдается |

Машина: Windows 10 x64, Core i7-2600, 32 GiB, .NET 6.0.36.
Все 15 последовательных результатов совпали с эталоном исходных CLI.
Для relay/federation параллельность дала пропуски событий первичных журналов;
совпадение агрегированного summary не закрыло эту ошибку. Неудачная попытка federation
не заменялась новой. Ошибка запрещает трактовать отношение времени как ускорение.

Причина ранних падений на Windows/Xeon подтверждена отдельной короткой диагностикой:
`DijkstraPathFinder.GetPath`, строка 58, одновременно записывает маршруты из
`Parallel.ForEach` в обычный `Dictionary`. .NET обнаруживает повреждение коллекции
в `Dictionary.TryInsert`; наружу runner выдавал только `Simulator refresh failed`.
В исходных неудачных попытках сбой происходил на connect, обычно на первом тике,
до подачи нагрузки. Полный диагностический runner воспроизвёл одно и то же
внутреннее исключение для relay, federation и load-balancer; отдельная облегчённая
проверка подключения его не воспроизвела. [Журнал и точная причина](../reports/audits/mqtty-benchmark-20261103/xeon-diagnosis.json).
Исправление модели и повтор бенчмарка не выполнялись. Исправление кэша само по себе
ещё не подтвердит корректность параллельной записи первичных событий.

Исполняются **копии** двух runners с переключением `Refresh(false/true, 0)` внутри
одного графа. Relay и federation используют тот же движок, что соответствующие
режимы Web; load-balancer — round-robin с четырьмя backend. Исходные классы модели
не меняются. Это отдельный тестовый режим: штатные runners используют serial.
Входы общие: четыре брокера/backend, 16 publishers, ideal, payload 256, QoS 1,
seed 42, tick 0,1 мс, прогрев 0,01 с, измерение 0,2 с при 1000 публикаций/с,
drain до 1 с. Сети имеют разную полезную работу/fanout, поэтому таблица не ранжирует алгоритмы.

На `10.100.101.14` (Эльбрус, Linux/e2k, .NET 6.0.10) и `10.10.144.66`
(Windows x64, Xeon E3-1230 v6, .NET 6.0.2) запущен **тот же пакет**:
6 пилотных + 30 учётных попыток, предел 600 с на попытку, 6 ч 10 мин на задание.
Старт контроллеров подтверждён после разрыва SSH. Завершение и окончательные
удалённые секунды пока не проверялись. Детали: [текущие задания](../reports/status/Linux-work-queue.md).

Квитанции: [все локальные попытки](../reports/audits/mqtty-benchmark-20261103/local-result.json),
[секунды и сравнения](../reports/audits/mqtty-benchmark-20261103/local-summary.json),
[окружение](../reports/audits/mqtty-benchmark-20261103/local-environment.json),
[точные входы](../reports/audits/mqtty-benchmark-20261103/cases.json),
[изменения тестовых runners](../reports/audits/mqtty-benchmark-20261103/adapter.json),
[SHA пакета](../reports/audits/mqtty-benchmark-20261103/package-receipt.json),
[запуск удалённо](../reports/audits/mqtty-benchmark-20261103/remote-launch.json),
[хэши локального архива и квитанций](../reports/audits/mqtty-benchmark-20261103/evidence-receipt.json).
Исходные архивы остаются вне Git. При сборе удалённых результатов проверить seals,
сохранить ошибки, добавить их в эту таблицу; не перезапускать ради успешного результата.

Подготовка нового отдельного пакета из gitlink-версий текущей `develop`:

```powershell
python reproducibility/prepare_benchmark.py --output reproducibility/artifacts/benchmark-new
python reproducibility/artifacts/benchmark-new/package/benchmark.py --mode preflight --output reproducibility/artifacts/benchmark-new/preflight
python reproducibility/artifacts/benchmark-new/package/benchmark.py --mode native --output reproducibility/artifacts/benchmark-new/native
python reproducibility/artifacts/benchmark-new/package/benchmark.py --mode pilot --baseline reproducibility/artifacts/benchmark-new/native/result.json --output reproducibility/artifacts/benchmark-new/pilot
```

Для `--mode main` нужны `--baseline` завершённого native/pilot и `--admission`:
JSON с `packageSha256` (SHA байтов `package/manifest.json`) и `timeoutSeconds`
(целое 1–600), зафиксированными до серии. `--mode campaign` добавляет перед main
шесть пилотных попыток; каждый процесс новый, порядок serial/parallel чередуется.
`--output` всегда новый. Serial с ошибкой или расхождением останавливает серию;
parallel с ошибкой сохраняется как диагностическая попытка и входит в пять попыток.
Запуск не делает автоматических повторов. Сводка:

```powershell
python reproducibility/summarize_benchmark.py <result-directory> --output <new-summary.json>
python -m unittest discover -s reproducibility -p test_benchmark.py -v
```

## Принятая проверка воспроизводимости Windows → Эльбрус

[Актуальный отчёт ALT Linux / Эльбрус от 02.11.2026](reports/linux-e2k-20261102/README.md):
**6/6 сценариев, 12/12 удалённых запусков**, все научные fingerprints совпали
с заново полученным Windows baseline. Различается только runtime: 6.0.10 / 6.0.36.
Прежние результаты и baseline заменены; они доступны в истории Git.

## Версии принятого эксперимента

- Метарепозиторий `develop` при сборке: `487b23c9269229a54246cf0013e9df40817ebbd1`.
- MqttY, закреплённый gitlink: `f5c0a4715b555c4348d2a2843177251f62c640ff`.
- MqttBenchmark, закреплённый gitlink: `ab513df3d2b65462abcfba8084f066ae4b1bd0a1`.
- Новый baseline: [baseline-windows-smoke.json](baseline-windows-smoke.json).
- Точный профиль: [broker-benchmark.v2.json](reports/linux-e2k-20261102/evidence/inputs/broker-benchmark.v2.json).

Брались версии подмодулей из root `develop`, а не произвольные более поздние
вершины их веток. Сборка выполнялась в отдельных чистых копиях. Для нового
эксперимента проверяйте gitlink-версии и чистоту tracked-файлов исходников.

## Проверка скачанного отчёта

Нужен только Python 3.8+, без SSH и .NET:

```bash
python3 reproducibility/verify_evidence.py reproducibility/reports/linux-e2k-20261102
```

Verifier проверяет 102 побайтовых SHA-256, все 12 исходных bundles, шесть сравнений
с baseline, источник сборки и профиль. Он заново вычисляет fingerprints из JSON,
а не доверяет только флагам итогового сравнения. Для evidence отключена Git-конверсия
окончаний строк, чтобы сохранить исходные байты.

## Что моделируется

Один брокер, один издатель и один подписчик; `brokerFidelity`, выбор профиля по
двум `connectedClients`. Два режима: ideal и предварительный legacy Mosquitto v2.
Payload 16 байт, seed 42, QoS 0/1/2; публикация и подписка используют одинаковый QoS.
Обработка последовательная: `parallel=false`, `refreshStrategy=0`.

Тик 0,1 мс; прогрев 0,1 с; измерение 1 с; drain до 2 с виртуального времени.
Offered RPS 1000; сеть 100 000 000 байт/с, очередь 100 000, transferTicks=1.
Каждый сценарий запускается дважды в отдельном процессе.

В новой серии attempted равен 1000 во всех сценариях. В legacy QoS0 воспроизводятся
49 отказов и 951 completed; остальные сценарии дают 1000 completed. Полные метрики,
гистограммы, конфигурации и границы фаз приведены в отчёте и исходных данных.

## Создание нового Windows baseline

`run.py` использует только стандартную библиотеку Python, Git и .NET SDK для net6.0.
Он собирает консольный проект из `mqtty/`, фиксирует SHA обоих репозиториев и
точных байтов профиля, выполняет 12 запусков и проверяет совпадение пар:

```powershell
python reproducibility/run.py --output reproducibility/artifacts/windows-new-001
```

Сохраняйте новый `fingerprints.json` вместе с исходниками, точным профилем и
готовой сборкой. Не заменяйте SHA старого baseline вручную: для нового кода нужна
новая выполненная серия. `--full` расширяет набор до payload 16/256 и seeds 42/43/44:
36 сценариев, 72 запуска. Расширенная матрица в опубликованном отчёте не выполнялась.

## Сборка локально и запуск на Linux

На Windows публикуется управляемая сборка без RID и apphost:

```powershell
dotnet publish mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.MqttRelay.App/EntityFX.MqttY.MqttRelay.App.csproj `
  -c Release --self-contained false -p:UseAppHost=false --output <new-package>/app
```

В пакет входят приложение со всеми опубликованными зависимостями, `run.py`,
`run_prebuilt.py`, новый baseline, точные байты профиля и Git bundle обоих исходных
репозиториев. Проверяйте SHA-256 архива и файлов перед запуском. Разверните bundle
в каталоги `mqtty/` и `MqttBenchmark/` в корне пакета: CLI требует настоящие чистые
Git-репозитории даже при запуске готовой DLL.

На Linux нужны Git, Python и совместимый runtime. Сборка на Linux не выполняется:

```bash
python3 reproducibility/run_prebuilt.py   --app app/EntityFX.MqttY.MqttRelay.App.dll   --profile profiles/broker-benchmark.v2.json   --baseline reproducibility/baseline-windows-smoke.json   --output results
```

Каталог результата должен отсутствовать. Профиль передавайте отдельным файлом:
CRLF и LF дают разные побайтовые SHA-256 даже при одинаковом JSON. В принятом
пакете профиль содержит LF. На проверенном сервере фоновый процесс не пережил
закрытия SSH, поэтому успешная серия выполнялась с удержанием SSH-соединения.
При обрыве сохраняйте частичную попытку отдельно и используйте новый output.

## Критерии и границы вывода

Строго сравниваются полная summary, брокерные counters, latency histogram, фазы,
подключения и научные параметры конфигурации. Нормализуются только порядок ключей
и форматирование JSON. Численные допуски и округление не применяются.

`run.py` сравнивает runtime вместе с остальными fingerprints; `run_prebuilt.py`
отдельно сообщает `runtimeMatches` и `scientificFingerprintsMatch`. Отличие runtime
не скрывается: опубликованный результат подтверждает равенство научных данных
в разных окружениях. Timestamps, run IDs, ОС, CPU, строковый общий dump counters
и формат топологии сохраняются, но не входят в критерий равенства.

CLI-код 3 означает непройденный reference-критерий. Все legacy-запуски имеют этот
код; ideal-запуски — 0. Успешный replay не доказывает fidelity предварительного
профиля `provisional-legacy-v1`. Модельные эксперименты не требуют реальных брокеров,
Docker или физической сети. Другие топологии, реальные брокеры, пропускная способность
оборудования, статистическая независимость повторов и побайтовая воспроизводимость
пересборки этим набором не проверяются.
