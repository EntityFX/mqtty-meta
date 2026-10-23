# Локальный стенд v3 на Linux и Windows

Этот маршрут использует один компьютер: native-брокер, контроллер и генератор нагрузки. Linux — Ubuntu/ALT; Windows — непосредственно для Aedes/Mosquitto/ActiveMQ, либо Ubuntu в WSL2 для полного набора с EMQX. Гостевой Linux и его NAT учитываются как отдельное окружение измерений, а не как эквивалент native Windows.

Нужны [подготовленная машина](machine-preparation.md), [установленный брокер](quick-start.md), чистая версия MqttBenchmark и свободный тестовый порт. Все команды этого руководства выполняются из корня MqttBenchmark. На Linux войдите в `pwsh` после `cd "$MQTTY_ROOT/MqttBenchmark"`; на Windows используйте PowerShell 7 и `Set-Location "$MqttyRoot/MqttBenchmark"`. Далее команды одинаковые.

## 1. Выбрать владельца процесса

| Режим | Подготовка | Действия контроллера | Остановка |
|---|---|---|---|
| `managed` | Брокер установлен; MQTT-порт свободен | Запускает процесс/launcher, проверяет PID и хэш, pinning, снимает телеметрию | Останавливает только идентифицированный процесс и его дочерние процессы |
| `external` | Оператор запускает брокер вручную и задаёт CPU affinity | Подключается к слушающему процессу, проверяет identity/config/affinity и снимает ту же телеметрию | Оставляет процесс оператору; оператор завершает его вручную |

Оба режима используют те же команды `preflight`, `matrix`, `aggregate`, те же артефакты v3 и формулы. Менять `lifecycle` во время кампании нельзя: это меняет хэш инвентаря. `external` не означает «только MQTT без телеметрии»: нужны права читать процесс на хосте брокера.

## 2. Подготовить описание брокера

Для полного набора в Linux/WSL используйте [spec четырёх брокеров](../../MqttBenchmark/docs/examples/four-brokers.linux.spec.json); замените абсолютные пути, версии и активные конфигурации. Для direct Windows есть [spec трёх брокеров](../../MqttBenchmark/docs/examples/three-brokers.windows.spec.json); для EMQX используйте полный Linux spec внутри WSL. Для Mosquitto выберите [Linux spec](../../MqttBenchmark/docs/examples/mosquitto.linux.spec.json) или [Windows spec](../../MqttBenchmark/docs/examples/mosquitto.windows.spec.json). Скопируйте его в локальный файл, затем замените executable и пути при необходимости:

```powershell
Copy-Item docs/examples/mosquitto.linux.spec.json config/brokers.spec.local.json
# Windows: вместо предыдущей строки копировать mosquitto.windows.spec.json.
Get-Content config/brokers.spec.local.json
```

Spec содержит входы для генератора, а не готовый инвентарь. Для Aedes поля задаются так:

```json
{
  "brokers": [{
    "name": "Aedes",
    "mqttUri": "mqtt://127.0.0.1:1883",
    "executable": "/usr/bin/node",
    "args": ["${MqttBenchmarkRoot}/docker/brokers/aedes/server.js", "1883"],
    "configFiles": ["${MqttBenchmarkRoot}/docker/brokers/aedes/server.js", "${MqttBenchmarkRoot}/docker/brokers/aedes/package-lock.json"],
    "versionArgs": ["--version"],
    "brokerVersion": "1.1.2"
  }]
}
```

В Windows executable обычно `C:/Program Files/nodejs/node.exe`. Действительный путь: `(Get-Command node).Source`. `versionArgs` сообщает версию executable; Node/Java не являются версией брокера. `brokerVersion` — отдельно зафиксированная версия дистрибутива. Для Mosquitto сверяйте её с выводом `mosquitto -h`.

Для Classic executable должен указывать на `java` / `java.exe`, `launcherExecutable` — на установленный `bin/activemq` / `bin/activemq.bat`, `args` — `["console"]` в Linux и `["start"]` в Windows, `workingDirectory` — каталог дистрибутива, `configFiles` — используемый `conf/activemq.xml`. В Windows environment.JAVA_HOME укажите каталог выбранного JDK/JRE17. Генератор фиксирует SHA-256 **обоих** executable и launcher; listener PID обязан принадлежать указанной JVM. Для EMQX в Linux executable — реальный `erts-*/bin/beam.smp`, launcher — `bin/emqx`, args — `["foreground"]`, workingDirectory — каталог дистрибутива; configFiles включают emqx.conf, base.hocon, vm.args и существующий cluster.hocon. Набор активных конфигурационных файлов определяется установленным дистрибутивом. Найдите runtime: `Get-ChildItem /opt/emqx/erts-*/bin/beam.smp`.

Launcher поддерживает процесс, отличный от файла запуска, но не отменяет проверку личности listener. Для Windows с EMQX создавайте **весь четырёхброкерный стенд внутри WSL2 Ubuntu**, с Linux-путями. [Подготовка Windows хоста](two-machine-stand.md) описывает публикацию его портов для отдельного контроллера.

## 3. Сформировать инвентарь скриптом

На машине брокера:

```powershell
./scripts/NewNativeStand.ps1 -BrokerSpecPath config/brokers.spec.local.json `
  -StandPath config/broker-stand.local.json -CampaignPath config/campaign.local.json `
  -CpuMode hostAllCores -Lifecycle managed
```

Ожидаются два новых JSON-файла; существующие не перезаписываются. Для pinning одного CPU задайте `-CpuMode singleCorePinned -CpuSet '0'`, подставив доступный logical CPU. Для ручного режима замените `-Lifecycle managed` на `-Lifecycle external` и заранее запустите процесс по следующему разделу. Значение 4 GiB — requested memory limit; наличие enforcement проверяется по provenance, см. [подготовку ресурсов](machine-preparation.md).

Генератор использует массив brokers из spec. Один брокер даёт 72 ключа; четыре — 288. При четырёх брокерах нужны порты Aedes 1883, Mosquitto 2883, Classic 3883, EMQX 4883, соответственно перенастроенные listeners. Для Mosquitto копируйте loopback-конфигурацию в локальный файл с `listener 2883 127.0.0.1`; этот файл включите в spec. Единственный брокер допускает 1883 или другой свободный порт.

## 4. Подготовить инвентарь руками

Вместо генератора скопируйте существующий native-инвентарь и кампанию в `config/*.local.json`. Структура:

```json
{
  "schemaVersion": "broker-stand.v1",
  "deploymentMode": "singleHostSequential",
  "cpuMode": "hostAllCores",
  "activeBroker": "Mosquitto",
  "dockerContext": "native-local",
  "controlInterface": "127.0.0.1",
  "networkMode": "host",
  "brokers": [{
    "name": "Mosquitto", "loaded": true, "serviceName": "mosquitto", "containerName": "mosquitto",
    "runtime": "native", "lifecycle": "external",
    "mqttUri": "mqtt://127.0.0.1:1883", "singleHostMqttPort": 1883, "distributedMqttPort": 1883,
    "managementUri": null, "executable": "/usr/sbin/mosquitto",
    "executableSha256": "REPLACE_WITH_64_HEX_SHA256",
    "args": ["-c", "${MqttBenchmarkRoot}/docker/brokers/mosquitto/mosquitto.local.conf"],
    "configFiles": ["${MqttBenchmarkRoot}/docker/brokers/mosquitto/mosquitto.local.conf"],
    "cpuset": "", "memoryLimit": 4294967296
  }]
}
```

Это шаблон с явным заменяемым hash; он не запускается до подстановки. В Windows замените executable реальным `mosquitto.exe`. Хэш можно записать вручную из `(Get-FileHash -Algorithm SHA256 $Executable).Hash.ToLowerInvariant()` либо использовать:

```powershell
./scripts/PinNativeExecutable.ps1 -StandPath config/broker-stand.local.json -Executable /usr/sbin/mosquitto
```

В кампании должны совпадать deploymentMode/cpuMode и набор brokers; `customImageBrokers` задайте `[]`, для loopback все network thresholds — `null`. Для `hostAllCores` cpuset пустой. [Полный контракт](campaign-v3.md) — источник этих правил.

### Ручной старт процесса

Запустите брокер из [быстрого старта](quick-start.md) в отдельном терминале и проверьте MQTT. В `hostAllCores` дополнительный pinning не нужен. В `singleCorePinned` Linux запускает процесс через `taskset -c 0 <executable> <args>`; это наследуется JVM/Erlang. В Windows после старта найдите listener и задайте affinity **владельцу порта**, а не cmd launcher:

```powershell
$BrokerPort = 1883
$BrokerCpu = 0
$BrokerProcessId = (Get-NetTCPConnection -LocalPort $BrokerPort -State Listen | Select-Object -First 1).OwningProcess
(Get-Process -Id $BrokerProcessId).ProcessorAffinity = [IntPtr](1L -shl $BrokerCpu)
```

Контроллер внешнего процесса affinity не меняет: несовпадение с инвентарём отвергается. `stop`/`reset` в external-режиме не убивают процесс; очистку данных и перезапуск между повторами выполняет оператор. Для полной ручной серии разбейте вызовы `matrix` по брокерам через `--broker`, сохранив одну кампанию и неизменный инвентарь; перед следующей группой остановите предыдущий брокер. Автоматический reset внешней службы не подразумевается.

На малом Windows-хосте совместная нагрузка контроллера и брокера может превышать системный CPU guard70%; такой запуск отклоняется. Для `singleCorePinned` можно разделить CPU: например, брокер на logical CPU0 (`-CpuSet '0'`), контроллер на CPU2. Перед вызовом CLI/`RunNativeCampaign.ps1` в его PowerShell-терминале задайте `(Get-Process -Id $PID).ProcessorAffinity = [IntPtr]4`; дочерний CLI наследует маску. Эти номера применимы только если исходная маска разрешает оба CPU; выберите другие номера на своей машине. Windows managed broker создаётся независимо через WMI и получает CPU из инвентаря. В external-режиме оператор сам назначает брокеру маску1. Используйте одинаковый `cpuMode` в campaign и stand; фактические маски, процессорную топологию и совместное размещение сохраните с результатами. Порог guard не повышают ради успешного запуска; если ресурсы всё равно перегружены, перенесите контроллер на отдельную машину.

## 5. Проверить и измерить

Сначала dry-run: он не запускает брокер и не проверяет executable hash.

```powershell
dotnet run -c Release --project src/EntityFX.MqttBenchmark.Cli -- matrix `
  --config config/campaign.local.json --stand config/broker-stand.local.json `
  --campaign artifacts/local-pilot-001 --dry-run
dotnet run -c Release --project src/EntityFX.MqttBenchmark.Cli -- preflight `
  --config config/campaign.local.json --stand config/broker-stand.local.json `
  --output artifacts/local-preflight-001 --benchmark-repo .
dotnet run -c Release --project src/EntityFX.MqttBenchmark.Cli -- matrix `
  --config config/campaign.local.json --stand config/broker-stand.local.json `
  --campaign artifacts/local-pilot-001 --max-runs 1 --benchmark-repo .
```

Ожидаются успешный preflight, одна sealed success attempt и её telemetry. Код возврата 0 у пилота не означает полноту матрицы. Ctrl+C отменяет текущий запуск, сохраняет частичную попытку и освобождает управляемый брокер; external-брокер остаётся оператору. Если preflight не пройден — исправьте причину, выберите новый output-каталог и повторите; старый preflight не перезаписывается.

После принятия пилота продолжите ту же кампанию без изменения входов:

```powershell
dotnet run -c Release --project src/EntityFX.MqttBenchmark.Cli -- matrix `
  --config config/campaign.local.json --stand config/broker-stand.local.json `
  --campaign artifacts/local-pilot-001 --resume --benchmark-repo .
dotnet run -c Release --project src/EntityFX.MqttBenchmark.Cli -- aggregate `
  --config config/campaign.local.json --stand config/broker-stand.local.json `
  --raw artifacts/local-pilot-001 --output artifacts/local-aggregate-001
```

`aggregate` требует `--stand` и полного покрытия именно выбранной кампании. Для одного брокера это observations с 72 runs / 24 points; **MqttY calibrate такой файл не принимает**. Для переноса в MqttY соберите одну четырёхброкерную кампанию 288/96 с единой identity. Отдельные агрегаты брокеров нельзя просто склеить в JSON. Дальнейший шаг — [калибровка](../mqtty/calibration-v3.md).

## Автоматизированный маршрут тех же команд

После подготовки stand/campaign из корня MqttBenchmark в PowerShell 7:

```powershell
./scripts/RunNativeCampaign.ps1 -ConfigPath config/campaign.local.json `
  -StandPath config/broker-stand.local.json -CampaignDirectory artifacts/pilot-001 -Pilot
```

Скрипт выполняет preflight в новом соседнем каталоге и matrix с `--max-runs 1`. Ожидается один успешный ключ; aggregate не запускается. Без `-Pilot` выполняется полная матрица и aggregate в новом каталоге `<campaign>-aggregate`. Для продолжения той же кампании добавьте `-Resume`; для явного aggregate пути — `-AggregatedDirectory <new-dir>`. `-Broker Mosquitto` ограничивает preflight/matrix; при кампании с четырьмя брокерами этот выбор оставляет неполную матрицу, поэтому для поэтапного ручного запуска используйте отдельные CLI-команды, а aggregate вызывайте после завершения всех групп. Script не обходит требования clean Git, guard или identity. Ctrl+C вызывает cleanup CLI; external-процесс затем останавливает оператор.

Скрипт одинаков для локального и SSH-инвентаря. Команды CLI предыдущих разделов — ручной эквивалент. Для published пакета он автоматически выбирает `dotnet bin/EntityFX.MqttBenchmark.Cli.dll`; исходный репозиторий provenance задаётся через `-BenchmarkRepository`.

## Отдельные действия контроллера вручную

Для диагностики без matrix можно вызвать PowerShell controller actions с одним и тем же новым output. Inventory должен уже выбирать activeBroker/loaded; эти действия не создают научную кампанию сами по себе. Из MqttBenchmark в PowerShell7:

```powershell
$Stand = 'config/broker-stand.local.json'
$Output = 'artifacts/manual-controller-001'
./scripts/BrokerStand.ps1 -Action validate -ConfigPath $Stand -OutputDirectory $Output
./scripts/BrokerStand.ps1 -Action start -ConfigPath $Stand -OutputDirectory $Output
./scripts/BrokerStand.ps1 -Action health -ConfigPath $Stand -OutputDirectory $Output
./scripts/BrokerStand.ps1 -Action clock -ConfigPath $Stand -OutputDirectory $Output
./scripts/BrokerStand.ps1 -Action capture -ConfigPath $Stand -OutputDirectory $Output -CaptureSeconds 3
./scripts/BrokerStand.ps1 -Action stop -ConfigPath $Stand -OutputDirectory $Output
```

Ожидаются provenance, health/clock и три telemetry samples. Stop выполняйте также при ошибке capture; external-процесс остаётся оператору. Для контейнеров перед validate/start нужен `-Action pull` (custom build или pinned registry image). Snapshot входов/owner при ручных SSH вызовах привязан к output path; для измерительных кампаний используйте CLI с уникальным session owner и immutable journal. Одновременное управление одним стендом ручными и автоматическими вызовами не допускается.
