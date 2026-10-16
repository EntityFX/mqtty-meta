# Развёртывание исправленной модели на Linux

Новая отдельная кампания: `overnight-20261016-service-v1`. MqttY runtime и
чистый checkout — `115885e012053d0112413e07743b061369ebea93`, метод Fidelity
`empiricalServiceBudget`, closed-loop, tick 10 мкс. Relay сохраняет fixed/open-loop
и tick 100 мкс. Пороги не меняются. Предел остаётся **17 октября 08:00 МСК**;
ожидание переключения и повторный запуск не продлевают его.

Предшественник — `overnight-20261016-v1`. Его результаты старой модели не
переносятся в индексы новой модели. Полные натурные матрицы из 72 уникальных
успешных ключей могут использоваться повторно с исходной campaign identity,
конфигурацией и архивами. Неудачные попытки сохраняются. Неполные матрицы не
смешиваются с новыми: источник и причина отказа от заимствования перечислены
в `native-origins.json`. Замороженный профиль Aedes переносится побайтно.

Код калибровки не менялся, поэтому для совместимости профилей Mixed сохраняется
отдельно закреплённый калибратор f61daa4. Симулятор исполняет исправленную 115885e.
Исходные campaign identities, commits и SHA-256 не объединяются и не переписываются.

У Aedes прежний контроль остановился после 24 успешных измерений: два отказа
`Telemetry does not cover the remote workload completion fence` в точке
256 байт/QoS0/N=128. Нагрузка новой validation остаётся 20 секунд. Для новых
натурных этапов reserve захвата увеличен с 3 до 18 секунд (envelope 54 с);
заимствованные полные матрицы сохраняют свой прежний reserve. Контроль с новым
input hash выполняется заново, а не дополняет старые 24 строки.
Проверка старых ZIP показала: fence опережает последний sample на 0,01344 и
0,13520 секунды. Это ошибка покрытия хвоста capture, а не провал полноты
двадцатисекундного measurement; guard не ослаблен.

Переключение выполняет `tools/campaign/service-revision-handoff.py`:

1. Ждёт отсутствия active benchmark/simulation в старом контроллере.
2. Проверяет PID владельца unit, приостанавливает только этот controller,
   повторно проверяет отсутствие дочерних процессов и admission race.
3. Останавливает старый unit; его owned cleanup завершает только собственные
   оставшиеся broker processes. Записывает причину `superseded`.
4. Проверяет mount, free space, SHA архивов, соответствие inputs/journal и
   переносит только полные native-матрицы. Повторная агрегация из ZIP для
   ещё не проверенной матрицы выполняется штатным runner до профилирования.
5. Фиксирует новые inputs/source/DLL hashes до workload и запускает отдельный
   bounded systemd controller с абсолютным deadline и лимитом памяти 8 GiB.

Активный Mosquitto matrix не прерывается. Поэтому между deployment и новым
workload возможна очередь. `handoff-status.json` отличает waiting, switched,
deadline exhausted и failed с причиной; `controller-service.json` появляется
при запуске нового контроллера. Параллельных native-контроллеров нет.

Все входы, source snapshots, runtime, архивы и scratch находятся на hd250.
Проверяются `/dev/sdc1`, ext4, mount и минимумы 20 GiB/10 GiB на evidence/system.
Старые raw/архивы не удаляются. Возобновление старой кампании после handoff
не выполняется автоматически, чтобы не получить два генератора нагрузки.

Состояние новой кампании:
`/run/media/entityfx/hd250/mqtty-evidence/overnight-20261016-service-v1/controller/`.
Проверки до развёртывания: 118 MqttY tests, 45 Python tests. Сравнение Aedes на
Windows: 18/18 completed, 15 passed, три p99 failed из-за различия pooled p99 и
mean-of-repeat-p99; это ограничение сохраняется явно и не исправляется подгонкой.

## Фактический deployment

Runtime размещён в `tools/simulator-service-115885e-runtime`, исходники — в
`tools/mqtty-service-115885e` на hd250. CLI SHA-256:
`60c5a79fca5291b061af345f3fb98e758c62ae551763693d5f39f74d40e9cebd`.
Все runtime-файлы сверены с проверенным архивом; clean checkout указывает на
115885e. Linux load check не запускал симуляцию во время native-измерений;
его результат хранится в `tools/service-deploy-20261016/linux-load-check.json`.
Три теста whole-matrix reuse/partial-control protection/idle boundary повторены
на Linux и прошли.

Автоматическое переключение запущено 16 октября в 22:52 МСК, unit:
`mqttbenchmark-service-handoff-57b24d7c6e4248ed8df4555c8aa8689c.service`.
При запуске наблюдателя состояние было `waiting-for-idle-boundary`; активна
натурная калибровка Mosquitto предшественника. Это не объявляется стартом новой
нагрузки: реальный запуск подтверждают `handoff-status.json: switched` и
новый `controller-service.json` вместе с `state.json: running`.

Локальная копия статусов и будущих отчётов обновляется каждые 30 секунд в
`MqttBenchmark/artifacts/overnight-20261016-service-v1/`.
