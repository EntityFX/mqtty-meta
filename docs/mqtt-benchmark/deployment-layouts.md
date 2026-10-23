# Размещение: 1, 2 и 3+ машины

Место исполнения и runtime — независимые настройки. Контроллер — CLI, MQTT publishers/subscriber и хранилище raw; broker host — процессы или Linux-контейнеры. Каждая физическая машина может иметь Linux или Windows. EMQX на Windows требует Linux окружения WSL2 либо Linux-контейнеров; native Windows EMQX не поддерживается поставщиком.

| Размещение | Роли | deploymentMode | Native | Контейнеры |
|---|---|---|---|---|
| 1 машина | Контроллер и брокеры на одном хосте | singleHostSequential | [Локальный запуск](local-stand.md) | Local Docker context, Linux containers |
| 2 машины | Контроллер + общий хост брокеров | singleHostSequential | [SSH worker](two-machine-stand.md), root nativeHost | Remote Docker context общего хоста |
| 3+ машины | Контроллер + несколько хостов брокеров | distributedHosts | nativeHost каждого broker | dockerContext каждого broker |

Количество брокеров не равно количеству машин. Четыре брокера могут по очереди работать на одном хосте либо быть распределены по нескольким. **Matrix v3 выполняет измерения последовательно**, даже в distributedHosts: это размещение экспериментов, не автоматический эксперимент связанной сети реальных брокеров. Сеть симулируемых брокеров создаётся в [MqttY](../mqtty/network-experiments.md).

## Native на 3+ машинах: через скрипты

Подготовьте контроллер и каждый broker host по [подготовке ОС](machine-preparation.md), [установке брокеров](quick-start.md) и [SSH](two-machine-stand.md). SSH/data endpoints должны быть доступны контроллеру. Для каждого брокера на **его машине** создайте one-broker spec, затем stand/campaign с NewNativeStand.ps1 и NativeHostPath. Все источники используют одинаковый CPU mode; pins относятся к бинарным файлам именно этого хоста. MQTT-порт может быть 1883 на разных IP; endpoints обязаны быть уникальными.

На каждом broker host, PowerShell 7 из MqttBenchmark:

```powershell
./scripts/NewNativeStand.ps1 -BrokerSpecPath config/brokers.spec.local.json `
  -StandPath config/one-broker.local.json -CampaignPath config/one-campaign.local.json `
  -NativeHostPath config/native-host.local.json -CpuMode hostAllCores -Lifecycle managed
```

NativeHost JSON задаётся как в двухмашинном руководстве, пути SSH keys относятся к будущему контроллеру. Каждый source stand содержит один брокер. Перенесите файлы stand на контроллер через scp, под разными именами; campaign отдельных хостов не объединяют с результатами.

На контроллере из MqttBenchmark:

```powershell
./scripts/MergeNativeStands.ps1 `
  -InputStandPaths config/aedes-export.local.json,config/mosquitto-export.local.json,config/activemq-export.local.json,config/emqx-export.local.json `
  -StandPath config/distributed-stand.local.json -CampaignPath config/distributed-campaign.local.json
./scripts/RunNativeCampaign.ps1 -ConfigPath config/distributed-campaign.local.json `
  -StandPath config/distributed-stand.local.json -CampaignDirectory artifacts/distributed-pilot-001 -Pilot
```

Ожидаются merged distributedHosts config/stand и один successful pilot key после preflight всех выбранных брокеров. Root nativeHost не нужен; worker выбирается по active broker. Broker nativeHost имеет приоритет над root nativeHost. Разные Windows/Linux абсолютные пути не разрешаются на чужой машине: их проверяет соответствующий worker. Lease защищает выбранный workRoot; у каждого физического стенда используйте единый workRoot для всех контроллеров.

## Native на 3+ машинах: руками

Вместо MergeNativeStands.ps1 соберите новый JSON по stand.v1: deploymentMode=distributedHosts, brokers — записи из one-broker exports; каждой добавьте nativeHost своего хоста и уникальный dockerContext alias, например native-ssh-host-a. Alias нужен для lease и provenance; Docker им не запускается. Root dockerContext тоже остаётся обязательным schema field. Выставьте ровно один loaded=true, соответствующий activeBroker. Для нескольких brokers singleHostMqttPort — стандартные 1883/2883/3883/4883 (неиспользуемая здесь однохостовая карта); distributedMqttPort — фактический port данного endpoint. Для каждого runtime=native, executable/launcher hashes вычислены на broker host.

Campaign: schemaVersion3, deploymentMode=distributedHosts, тот же cpuMode, четыре стандартных broker names, customImageBrokers=[], полная матрица и явные guard thresholds. Ручные preflight/matrix/resume/aggregate — [локальный CLI runbook](local-stand.md) с этими новыми config/stand. Для ручных broker processes задайте lifecycle=external и предварительно запустите нужный брокер на соответствующем хосте. CLI stop оставляет external process оператору. Ctrl+C выполняет managed cleanup выбранного worker; потерянный SSH требует recovery lease.

## Контейнерные варианты

Основной quick start пакета — без Docker. Контейнерный runtime уже существует в v3: при отсутствии runtime entry считается container, а nativeHost не используется для такого active broker. Контейнеры этих брокеров — **Linux containers**, телеметрия использует Linux cgroup/procfs. Linux Engine поддерживает host networking; Windows Docker Desktop 4.34+ требует Linux backend и явно включённого host networking, либо Docker Engine внутри WSL2 с forwarding по двухмашинному руководству. Windows containers не поддержаны этим контрактом. [Официальные границы Docker host network](https://docs.docker.com/engine/network/drivers/host/).

На контроллере требуется Docker CLI + Compose plugin; на broker host — доступный Linux Docker Engine. На Linux ставьте Engine по инструкции своей ОС; на Windows — Docker Desktop с указанным backend. Контроллер Windows может управлять Linux Engine, даже если его собственный daemon не используется. Для удалённого Engine через SSH учётная запись должна иметь доступ к его socket. После проверки SSH с ключом и known_hosts, из MqttBenchmark на контроллере:

```powershell
docker context create broker-host-a --docker 'host=ssh://researcher@192.0.2.20'
docker --context broker-host-a info
docker compose version
```

Ожидается удалённый Linux Engine; `192.0.2.20` заменить реальным endpoint. Для других hosts создайте отдельные contexts. [Официальный SSH transport Docker](https://docs.docker.com/engine/security/protect-access/). На Windows broker host SSH endpoint для этого способа — Linux WSL с Docker Engine; вариант прямого remote Desktop требует отдельной настройки daemon доступа.

Скопируйте config/broker-stand.v1.json в config/container-stand.local.json. Для одной/двух машин оставьте singleHostSequential и задайте root dockerContext нужного Engine; mqttUri — loopback либо data IP broker host с 1883/2883/3883/4883. Для 3+ машин задайте distributedHosts, у каждой broker записи dockerContext своего Engine и mqttUri его data IP:1883. controlInterface должен существовать в Linux broker environment для management bind; удобнее 127.0.0.1 с managementUri loopback и отдельным SSH доступом к UI. Согласуйте campaign deploymentMode/cpuMode. Не заменяйте проверенные digest выдуманными; для custom Aedes/Classic контроллер сохраняет реальные build inputs/image ID.

Через CLI выполните те же preflight/matrix/resume/aggregate из local runbook. Для контейнерного campaign сохраните customImageBrokers=[Aedes,ActiveMQ]. Native-generated config с пустым customImageBrokers нельзя механически применять к custom контейнерам без этой настройки. Script RunNativeCampaign.ps1 делегирует CLI и работает с таким inventory, но не устанавливает Docker. Артефакты container-cgroup CPU и native process-pinned CPU имеют разные scopes; результаты разных runtimes сохраняйте отдельными кампаниями.

Для ручного управления **контроллером контейнеров** используйте BrokerStand.ps1 -Action pull/validate/start/health/clock/capture/stop/reset с конкретными config/output, как в manual controller разделе local runbook. Это запуск через существующие controller actions с сохранённой provenance; произвольный docker run без них не создаёт требуемую v3 stand evidence. Остановка — CLI Ctrl+C либо controller stop; reset удаляет только заданный контейнер. Native external lifecycle не переносится на контейнеры.

## Статус и сравнимость

Native local четыре брокера проверены на текущей ALT; SSH и выбор per-broker host проверяются функциональными tests. Запуск нескольких отдельных физических машин, Windows/WSL и новых container layouts здесь не проведён; сохранённые исторические контейнерные серии описаны в [результатах](../../reports/history/setup/platform-results.md). Возможность ОС/vendor и фактическая acceptance конкретного стенда — разные статусы.

Смена числа машин, runtime, версии, NAT, CPU mode или сетевого пути требует новой серии. Нельзя объединять старые container и новые native points ради формальной полноты calibration. Полная матрица и fidelity подтверждаются отдельно от проверки запуска.
