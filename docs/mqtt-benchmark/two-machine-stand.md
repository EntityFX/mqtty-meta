# Стенд v3 на двух машинах

Контроллер выполняет CLI, создаёт publishers/subscriber и хранит raw-артефакты. Хост брокера выполняет PowerShell worker, запускает native-процесс либо подключается к процессу оператора. SSH переносит управление и телеметрию; MQTT идёт непосредственно между контроллером и listener. Docker не требуется.

| Контроллер | Машина стенда | Исполнение |
|---|---|---|
| Linux | Linux | SSH → pwsh → native-брокер |
| Windows | Linux | Тот же SSH worker; Windows .NET CLI и NIC counters |
| Linux | Windows | OpenSSH Server → pwsh → Aedes/Mosquitto/Classic; для полного набора — SSH в Ubuntu WSL2 |
| Windows | Windows | Аналогично; разные физические машины и доступные MQTT-порты |

Общий процесс поддерживает обе ОС. Linux SSH transport проверен реальным изолированным OpenSSH. Linux-контроллер → физический Windows-стенд прошёл короткие кампании Mosquitto/Aedes/Classic; Windows-контроллер → физический Linux-стенд проверен для Mosquitto; Windows → Windows и WSL требуют отдельной acceptance. WSL2 — Linux guest на Windows, его использование фиксируется в manifest и не маскируется под native Windows. [Статусы проверок](../../reports/history/setup/platform-validation.md).

## 1. Подготовить роли

На контроллере установите [.NET, PowerShell, Git](machine-preparation.md) и SSH client. На Linux Ubuntu: `sudo apt-get install openssh-client`; ALT: `sudo apt-get install openssh-clients`. На Windows в административном PowerShell, если компонента нет:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
```

На broker host нужны pwsh и выбранные брокеры; SDK бенчмарка не обязателен. Разместите broker assets в собственном каталоге, например `/home/researcher/mqtty-meta/MqttBenchmark` либо `C:/work/mqtty-meta/MqttBenchmark`, и рабочие каталоги с правом записи. Пути инвентаря относятся к **ОС broker host**, пути SSH key/known_hosts — к **ОС контроллера**.

Linux SSH server: Ubuntu `sudo apt-get install openssh-server`, `sudo systemctl enable --now ssh`; ALT `sudo apt-get install openssh-server`, `sudo systemctl enable --now sshd`. Используйте обычную выделенную учётную запись с правами на свои брокеры и артефакты.

Windows native server, в административном PowerShell:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service sshd -StartupType Automatic
```

PowerShell 7 должен быть доступен для SSH-учётной записи как `pwsh`. Проверьте его из настоящего SSH-сеанса, не только интерактивно через desktop. Контроллер вызывает `pwsh -NoProfile -EncodedCommand`; отдельный PowerShell remoting subsystem не нужен. Пути с пробелами для remote pwshExecutable не поддерживаются: используйте `pwsh` на PATH или alias/junction без пробелов. Источник настройки сервера: [Microsoft OpenSSH](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-server-configuration).

Для обычного Windows пользователя public key находится в `.ssh/authorized_keys` его профиля. Для группы Administrators OpenSSH обычно использует `C:/ProgramData/ssh/administrators_authorized_keys` с ограниченными ACL; порядок настройки — [официальное руководство ключей](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement). Private key хранится только на контроллере.

## 2. Проверить SSH вручную

На контроллере Linux, из любого каталога:

```bash
ssh-keygen -t ed25519 -f "$HOME/.ssh/mqtty_stand"
ssh-copy-id -i "$HOME/.ssh/mqtty_stand.pub" researcher@192.0.2.20
ssh -i "$HOME/.ssh/mqtty_stand" researcher@192.0.2.20
```

В Windows контроллере:

```powershell
ssh-keygen -t ed25519 -f "$env:USERPROFILE/.ssh/mqtty_stand"
ssh -i "$env:USERPROFILE/.ssh/mqtty_stand" researcher@192.0.2.20
```

`192.0.2.20` — пример; подставьте IP стенда. Windows public key перенесите в authorized_keys штатным способом. При первом подключении сверяйте fingerprint с хостом; затем контроллер использует `StrictHostKeyChecking=yes` и `BatchMode=yes`. Разблокируйте ключ через ssh-agent, если он с passphrase. В SSH-сеансе выполните `pwsh -NoProfile`, затем `$PSVersionTable.PSVersion`, `Get-Command node` / путь брокера. Ожидается PowerShell 7, доступный executable и права чтения конфигураций; выход — `exit`, затем `exit` из SSH.

## 3. Проверить MQTT вручную

На broker host скопируйте конфигурацию в локальный файл. Listener привяжите к data IP стенда либо к `0.0.0.0`, если доступ ограничивает firewall; не оставляйте `127.0.0.1` для внешнего контроллера. Для Mosquitto: `listener 1883 0.0.0.0`. Для Classic: `mqtt://<data-IP>:3883`. Для EMQX: `listeners.tcp.default.bind = "<data-IP>:4883"`. Management listeners оставьте локальными.

Оператор запускает брокер командами [быстрого старта](quick-start.md). С контроллера выполните пары `mosquitto_sub` / `mosquitto_pub` того же руководства с host=IP стенда и выбранным port. В Windows используются `.exe` из установки Mosquitto. Ожидается `ok`. TCP-connect сам по себе не подтверждает MQTT CONNECT/SUBSCRIBE/PUBLISH.

Остановите ручной брокер перед managed-режимом либо оставьте его для external-режима. При ручной остановке действуют Ctrl+C / команды службы, описанные для каждого брокера. Firewall разрешает MQTT только контроллеру; existing SSH policy не заменяется.

## 4. Подготовить инвентарь скриптом или руками

На broker host создайте spec по [локальному runbook](local-stand.md), но mqttUri должен указывать на адрес, доступный **контроллеру**. executable/launcher/configFiles — абсолютные пути broker host. Генератор запускается на broker host, чтобы вычислить хэши его бинарей.

Сформируйте пару stand/campaign через `NewNativeStand.ps1` с `-Lifecycle managed` либо `external`. Перенесите эти JSON на контроллер через `scp`; в Windows также есть `scp.exe`. Затем на контроллере добавьте в новый stand файл `nativeHost`:

```json
{
  "host": "192.0.2.20",
  "user": "researcher",
  "port": 22,
  "repositoryRoot": "/home/researcher/mqtty-meta/MqttBenchmark",
  "workRoot": "/home/researcher/mqtty-stand-work",
  "pwshExecutable": "pwsh",
  "identityFile": "/home/controller/.ssh/mqtty_stand"
}
```

Это объект `nativeHost`, а не полный stand. Для Windows broker host замените repositoryRoot/workRoot на `C:/...`; для Windows контроллера identityFile — на `C:/Users/.../.ssh/mqtty_stand`. `knownHostsFile` и `sshExecutable` можно задать явно. Приватный ключ не включается в JSON; в нём только путь. Все brokers в таком инвентаре имеют runtime native. Проще добавить nativeHost сразу, передав генератору `-NativeHostPath <json-file>`; содержимое должно отражать пути будущего контроллера.

На Windows-контроллере transport по умолчанию выбирает `usr/bin/ssh.exe` из установленного Git for Windows, обнаруженного через `git` на PATH. Системный OpenSSH остаётся сервером на Windows broker host. Это разные роли: в проверенном Windows SSH-сеансе системный клиент9.5p2 зависал на перенаправленном stdin, а клиент Git10.5p1 передал worker успешно. Явный `nativeHost.sshExecutable`, например `C:/Program Files/Git/usr/bin/ssh.exe`, имеет приоритет; пробелы в этом локальном пути допустимы. Если Git SSH не найден, используется `ssh` на PATH — его передачу stdin нужно проверить до кампании. На Linux по умолчанию используется `ssh` на PATH.

Для подготовки руками используйте структуру [инвентаря](local-stand.md), вычислите SHA-256 на broker host, заполните launcher identity и добавьте тот же nativeHost. Режимы CPU/deployment обязаны совпадать с кампанией. Один удалённый host использует `singleHostSequential`; `distributedHosts` обозначает несколько broker hosts, не просто пару «контроллер + стенд». [Маршрут 3+ машин](deployment-layouts.md).

## 5. Запуск через CLI

На контроллере, из MqttBenchmark, в PowerShell 7:

```powershell
dotnet run -c Release --project src/EntityFX.MqttBenchmark.Cli -- preflight `
  --config config/campaign.local.json --stand config/broker-stand.local.json `
  --output artifacts/remote-preflight-001 --benchmark-repo .
dotnet run -c Release --project src/EntityFX.MqttBenchmark.Cli -- matrix `
  --config config/campaign.local.json --stand config/broker-stand.local.json `
  --campaign artifacts/remote-campaign-001 --max-runs 1 --benchmark-repo .
```

Ожидаются remote clock-alignment, process-pinned telemetry, local telemetry-ready до начала нагрузки и remote fence после её завершения. Raw-артефакты записываются на контроллере; worker evidence остаётся также в workRoot на broker host. Продолжение `--resume` и агрегация — как в [локальном runbook](local-stand.md). В ручном режиме для каждой группы `--broker` оператор должен заранее запустить соответствующий процесс. Новые попытки имеют отдельные каталоги; исходную identity между группами не меняют.

Для проверки часов worker после запуска и проверки executable выполняет три обмена с контроллером через открытое SSH-соединение. Каждый remote timestamp находится между отправкой запроса и получением ответа; uncertainty включает половину времени обмена, 1 мс на разрешение часов и расхождение локального wall clock с монотонным таймером. Каждый обмен и итоговая оценка должны укладываться в `ClockAlignmentBoundMs` (по умолчанию 3000 мс), а локальное расхождение таймеров — в 100 мс. Метод `ssh-native-clock-roundtrip` и все три пробы сохраняются в `clock-alignment.json`. Холодный запуск PowerShell и хэширование executable происходят до этих обменов. Ошибка проверки останавливает preflight: синхронизируйте часы и проверьте задержки транспорта перед новой попыткой.

SSH-lease в `workRoot/active-stand.json` предотвращает одновременное использование хоста разными контроллерами. После штатной managed-остановки процесс и lease освобождаются; external-процесс остаётся, lease освобождается. Если контроллер аварийно исчез, проверьте remote run-provenance и PID/start identity, сохраните доказательства, остановите принадлежащий этому тесту процесс и только затем удалите его stale lease. Не удаляйте lease живой кампании. Ctrl+C запускает штатную попытку остановки; при потере SSH связь надо восстановить для cleanup.

Для запуска скриптом вместо этих CLI-команд используйте `RunNativeCampaign.ps1` из [локального руководства](local-stand.md#автоматизированный-маршрут-тех-же-команд), с теми же config/stand и новым campaign directory. Режим external требует заранее работающего процесса на удалённом хосте; script не запускает его вместо оператора.

## 6. Полный набор на Windows через WSL2

Нужна Windows с WSL2: для простого wsl --install используйте Windows10 build19041+ либо Windows11; старые x64 builds18362.1049+ требуют ручного метода Microsoft. **Build17763/LTSC2019 не поддерживает WSL2**. На нём native Aedes/Mosquitto/Classic остаются отдельным маршрутом; полный EMQX требует Linux VM либо более новой Windows. [Требования WSL2](https://learn.microsoft.com/en-us/windows/wsl/install-manual). Обновление ОС/включение виртуализации и reboot согласуются отдельно от запуска эксперимента.

На поддерживаемом Windows broker host, в административном PowerShell:

```powershell
wsl --install -d Ubuntu-24.04
wsl --list --verbose
```

Завершите настройку Linux пользователя, затем `wsl -d Ubuntu-24.04`. Внутри WSL выполните подготовку Ubuntu, установку четырёх брокеров, SSH server и создайте Linux-инвентарь. Для однохостового полного эксперимента запускайте также CLI **внутри WSL**, по loopback; direct Windows CLI к виртуальному WSL NIC не проходит требование физического NIC для удалённого data path.

Для отдельного контроллера публикуйте SSH и MQTT на IP **Windows broker host**. Например, в административном PowerShell:

```powershell
$StandIp = '192.0.2.20' # реальный data/control IP Windows
$ControllerIp = '192.0.2.10'
$WslIp = (wsl -d Ubuntu-24.04 hostname -I).Trim().Split(' ', [StringSplitOptions]::RemoveEmptyEntries)[0]
netsh interface portproxy add v4tov4 listenaddress=$StandIp listenport=2222 connectaddress=$WslIp connectport=22
foreach ($Port in @(1883,2883,3883,4883)) {
  netsh interface portproxy add v4tov4 listenaddress=$StandIp listenport=$Port connectaddress=$WslIp connectport=$Port
}
New-NetFirewallRule -DisplayName 'MqttY stand from controller' -Direction Inbound -Action Allow `
  -Protocol TCP -LocalAddress $StandIp -RemoteAddress $ControllerIp -LocalPort 2222,1883,2883,3883,4883
netsh interface portproxy show v4tov4
```

В WSL listeners привяжите к `0.0.0.0`, в controller mqttUri — Windows IP и соответствующий port; nativeHost.host — Windows IP, port — 2222, user/paths — **Linux WSL**. После перезапуска WSL его IP может поменяться: до следующей кампании обновите forwarding и повторите проверки; во время кампании не меняйте маршрут. Остановка — через CLI/оператора внутри WSL. Удаляйте созданные portproxy правила точечно через `netsh interface portproxy delete v4tov4 listenaddress=... listenport=...`, firewall правило — `Remove-NetFirewallRule -DisplayName 'MqttY stand from controller'`.

Источник: [WSL networking](https://learn.microsoft.com/en-us/windows/wsl/networking). Windows forwarding/NAT остаётся частью измеряемой установки, его RTT и дополнительные ограничения записываются вместе с профилем.
