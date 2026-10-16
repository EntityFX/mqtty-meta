# Исправление модели при прежних допусках

MqttY commit: `115885e012053d0112413e07743b061369ebea93`.
Пороговые критерии остаются прежними: RPS 15%, p99 20%, абсолютная разность
failure/loss 0,05. Профили не перекалибровываются по результатам контроля.

Исправлены четыре механизма:

1. Echo RTT не приравнивается к PUBACK/PUBCOMP. В новом режиме
   `transportTiming=empiricalServiceBudget` protocol floor дополнительно
   ограничен минимальной publish latency исходной калибровочной CDF.
   Нижняя граница, задаваемая tick и самим протоколом, сохраняется; наблюдения
   ниже неё не удаляются, их число отражено в `clippedEmpiricalSamples`.
2. Capacity pacing откладывает короткие всплески вместо немедленного создания
   искусственных `rateRejected`. Допуск режима — только single-broker
   BrokerFidelity с `loadMode=closedLoopPerPublisher`: у publisher не более
   одной ожидающей QoS1/2 публикации. Native failure probability, delivery loss,
   сетевые очереди и конечный drain остаются активными. Это модель backpressure,
   а не идентифицированная внутренняя очередь реального брокера.
3. QoS0 не ожидает ACK и использует offered-load accumulator без ограничения
   числа публикаций одним publisher за refresh.
4. QoS1/2 сохраняют дробную часть токена при ограничении publisher backlog.
   Ранее 9051 RPS округлялись до 8333 RPS даже при достаточной скорости ACK.

Capacity eligibility и эмпирическая остаточная задержка объединены максимумом:
натурная end-to-end CDF уже включает очередь. Их суммирование повторно добавило
бы ожидание очереди. Это явное предположение агрегатной модели, а не доказанное
разложение RTT на физический транспорт и service time. Новый режим не разрешён
для Relay. Старые `fixed` и `calibratedRttBudget` доступны для воспроизведения.

## Проверка

118 тестов прошли: empirical profile/provenance, фиксированный seed,
независимость simulation от reference, client clock, warmup boundary,
capacity pacing, QoS0 и дробный QoS1 offered load, legacy calibration и Relay.
В расширенном наборе обнаружен прежний тест с отрицательным processingTicks,
падающий также на f61daa4; тест приведён к существующему контракту исключения.
Поведение очереди для отрицательных значений не менялось.

Отдельная Windows-выборка проверяет 18 сценариев шести уже изученных
калибровочных точек Aedes, по seeds 42/43/44. Native measurement 20 с;
simulation warmup/measurement/drain 1/2/5 с, tick 10 мкс. Профиль скопирован
побайтно с SHA-256 `e16c599ebf32dda761f84afa2e21c8d2fc29e95bbb31b987607d83906b347239`.

[Текущий отчёт, таблица и графики](../MqttBenchmark/artifacts/service-budget-20261016-v2/reports/Aedes-service-budget.md),
[manifest](../MqttBenchmark/artifacts/service-budget-20261016-v2/manifest.json),
[статус проверки](../MqttBenchmark/artifacts/service-budget-20261016-v2/state.json).
Отчёт явно указывает фактическое покрытие и все failed/notApplicable.
Источник сравнения — frozen overnight-20261016-v1; проверяются одинаковые
reference и численные limits. Первая промежуточная проверка сохранена в
`service-budget-20261016` со статусом superseded: 11/18 завершённых сценариев;
она остановлена после выявления дополнительной ошибки дробного токена.
Сравнения разных ревизий не объединяются в один результат.

Найдена отдельная проблема оцениваемой величины для 16 байт/QoS1/N=64:
native p99 повторов после округления bins — 3,771/3,776/6,827 мс, их среднее
4,791 мс; p99 объединённых raw samples — 3,799 мс. Модель использует pooled CDF,
исходная reference — среднее p99 повторов. Это разные статистики: точное
воспроизведение pooled CDF может не пройти прежний oracle. Предупреждение
не скрывается и reference не заменяется автоматически. Показ обеих статистик
проверен отдельным тестом; следующий протокол должен явно согласовать estimand
также для независимого контроля. Подгонять модельную задержку к среднему p99
вместо воспроизведения сохранённого распределения неправильно.

Это регрессия по calibration, **не новый независимый контроль** и не доказательство
точности всей матрицы или других брокеров. Подбор по seeds, удаление редких
задержек, изменение натурных observations и ослабление критериев не используются.

Проверочная выборка выполняется на Windows: ночные native измерения Linux
не пересекаются с её CPU/RAM нагрузкой. Исходная ночная серия сохраняет свои
runtime, source snapshots, профили и commits. Для применения нового режима
нужен отдельный cohort с новым manifest; уже полученные результаты не переименовываются.
Генератор будущей overnight-серии принимает `--transport-timing empiricalServiceBudget`;
для него нужен CLI MqttY 115885e или совместимый последующий. Прежний default
`calibratedRttBudget` оставлен для воспроизведения, выбор метода фиксируется в series.json.

```powershell
python tools/campaign/verify-service-budget.py `
  --baseline <путь к frozen overnight controller> `
  --output <новый отдельный каталог> `
  --runtime <CLI ревизии 115885e> `
  --repository <чистый MqttY 115885e> `
  --benchmark-repository <чистый MqttBenchmark> --workers 2

python tools/campaign/write-service-budget-report.py `
  --cohort <каталог этой проверки> --baseline <локальная копия overnight controller>
```
