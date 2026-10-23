# Подготовка машины для MQTT экспериментов

Подготовка выполняется один раз перед установкой брокеров. Для сборки нужен SDK, для опубликованной CLI — runtime. Брокер и генератор нагрузки на одной машине конкурируют за ресурсы; такой стенд подходит для пилота, но его результаты характеризуют всю выбранную установку.

## 1. Рабочие каталоги и исходники

На Linux, из домашнего каталога:

```bash
mkdir -p "$HOME/projects"
cd "$HOME/projects"
git clone --recurse-submodules https://github.com/EntityFX/mqtty-meta.git
export MQTTY_ROOT="$HOME/projects/mqtty-meta"
cd "$MQTTY_ROOT"
git submodule status
git -C MqttBenchmark rev-parse HEAD
git -C mqtty rev-parse HEAD
```

Если клон уже есть, задайте `MQTTY_ROOT` его абсолютным путём. Для повторения конкретного эксперимента используйте сохранённые commit SHA всех субрепозиториев, а не только имя ветки. В Windows используйте Git for Windows и аналогичный каталог, например `C:/work/mqtty-meta`.

Локальные конфигурации помещайте в `MqttBenchmark/config/*.local.json`, результаты — в `MqttBenchmark/artifacts/` или `campaigns/`. Эти пути игнорируются Git. Не редактируйте закоммиченный инвентарь для настройки конкретной машины. Перед измерением `git status --porcelain --untracked-files=no` в MqttBenchmark должен быть пустым; перед калибровкой — в MqttY, перед `experiment` — в обоих проектах.

## 2. Ubuntu 24.04 x86 64

Из любого каталога:

```bash
sudo apt-get update
sudo apt-get install git curl ca-certificates tar gzip unzip python3 iproute2 \
  procps openssl libicu74 libssl3 zlib1g libgssapi-krb5-2 libstdc++6
```

Текущие CLI имеют target framework `net6.0`. Он требует runtime 6.x и остаётся таким для воспроизведения существующего кода; установка только runtime 8/10 его не заменяет. Систему сборки в этом комплекте не мигрируем. Если runtime отсутствует, установите SDK в отдельный пользовательский каталог официальным скриптом:

```bash
mkdir -p "$HOME/opt/mqtty-tools"
cd "$HOME/opt/mqtty-tools"
curl -fL https://dot.net/v1/dotnet-install.sh -o dotnet-install.sh
bash dotnet-install.sh --version 6.0.428 --install-dir "$HOME/opt/mqtty-tools/dotnet"
export PATH="$HOME/opt/mqtty-tools/dotnet:$PATH"
dotnet --list-sdks
dotnet --list-runtimes
```

Для целевой машины без сборки вместо SDK: `bash dotnet-install.sh --runtime dotnet --version 6.0.36 --install-dir "$HOME/opt/mqtty-tools/dotnet"`. Скрипт не устанавливает системные зависимости. Ожидается `Microsoft.NETCore.App 6.0.x`. Вариант пользовательской установки подтверждён [документацией Microsoft](https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-install-script); здесь не проведена установка на чистую Ubuntu.

PowerShell 7 установите из Microsoft repository:

```bash
cd "$HOME/opt/mqtty-tools"
curl -fLO https://packages.microsoft.com/config/ubuntu/24.04/packages-microsoft-prod.deb
sudo dpkg -i packages-microsoft-prod.deb
sudo apt-get update
sudo apt-get install powershell
pwsh -NoProfile -Command '$PSVersionTable.PSVersion'
```

Источник: [PowerShell на Ubuntu](https://learn.microsoft.com/en-us/powershell/scripting/install/install-ubuntu). Зафиксируйте фактически установленную версию; обновление инструментов во время кампании меняет условия эксперимента.

## 3. ALT Linux 11.1 p11

Simply Linux и Workstation используют RPM-пакеты: команды Ubuntu с DEB-пакетами нельзя переносить буквально. Сначала проверьте ОС и подключённую ветку:

```bash
cat /etc/os-release
apt-cache policy powershell dotnet-sdk-6.0 dotnet-runtime-6.0
```

Затем, из любого каталога:

```bash
sudo apt-get update
sudo apt-get install git curl ca-certificates tar gzip unzip python3 iproute2 procps powershell
```

Если `dotnet --list-runtimes` уже показывает 6.x, используйте его. Если настроенный репозиторий предлагает нужные пакеты:

```bash
sudo apt-get install dotnet-sdk-6.0 dotnet-runtime-6.0
dotnet --list-sdks
dotnet --list-runtimes
pwsh -NoProfile -Command '$PSVersionTable.PSVersion'
```

Наличие версии определяется подключённой веткой и зеркалом; отсутствие candidate не исправляют установкой пакета Ubuntu. Пользовательский SDK можно установить той же командой `dotnet-install.sh` из раздела Ubuntu, затем проверить зависимости и фактический запуск. Для PowerShell без RPM есть [официальная установка из архива](https://learn.microsoft.com/en-us/powershell/scripting/install/alternate-install-methods): выбрать архив Linux x64 и зависимости под его версию, распаковать в отдельный каталог и выполнить `chmod +x pwsh`. Путь задать через `export MQB_STAND_SHELL=/absolute/path/to/pwsh`.

На текущей Simply Linux 11.1 обнаружены SDK 6.0.136 и runtime 6.0.36; обе CLI успешно собираются установленным SDK 10.0.200. Это проверка существующего окружения, а не установки пакетов на чистую p11. Команды `apt-cache policy` и `rpm -q` позволяют отделить наличие пакетов от их происхождения.

## 4. Windows

Нужны Git for Windows, PowerShell 7 (`pwsh.exe`) и .NET 6 runtime; для сборки — SDK с targeting pack net6.0. Загрузки: [PowerShell](https://learn.microsoft.com/en-us/powershell/scripting/install/install-powershell-on-windows), [.NET 6](https://dotnet.microsoft.com/en-us/download/dotnet/6.0). Выберите архитектуру процесса и системы, обычно x64.

В PowerShell 7:

```powershell
$MqttyRoot = 'C:/work/mqtty-meta' # заменить на свой путь
Set-Location $MqttyRoot
git submodule status
dotnet --list-sdks
dotnet --list-runtimes
$PSVersionTable.PSVersion
Get-Command pwsh
```

Если `pwsh` не находится: `$env:MQB_STAND_SHELL = 'C:/Program Files/PowerShell/7/pwsh.exe'`. Windows native-ветка существует в коде, но её новый физический запуск не проводился. Для удалённого хоста ограничения перечислены в [руководстве двух машин](two-machine-stand.md).

Для полного набора с EMQX нужен WSL2 или отдельная Linux VM. Windows10 LTSC2019/build17763 не поддерживает WSL2; минимальные x64 builds18362.1049+ и простой install на19041+/Windows11 описаны в [руководстве](two-machine-stand.md). Версию ОС проверяйте через Get-CimInstance Win32_OperatingSystem, не только название Windows.

## 5. Проверки перед пилотом

Из корня MqttBenchmark на Linux:

```bash
dotnet build src/EntityFX.MqttBenchmark.Cli -c Release --nologo
pwsh -NoProfile -Command '$PSVersionTable.PSVersion'
lscpu
free -h
df -h .
timedatectl status
ip -4 address
ip route
ss -ltnp
```

CPU выбирают из доступной affinity-маски процесса, например `taskset -pc $$`. Для `singleCorePinned` указывают ровно один доступный logical CPU; остальные используют для генератора и ОС. Pinning не означает эксклюзивного владения ядром. На Windows managed worker проверяет CPU брокера по system affinity mask: отдельная маска контроллера не должна запрещать CPU независимого WMI-процесса брокера. Ограничения ОС при назначении affinity по-прежнему проверяются фактическим вызовом. Режим `hostAllCores` требует пустого `cpuset`. В native-коде лимит памяти 4 GiB записывается как настройка, но принудительное ограничение реализуется лишь best effort: проверяйте `memoryLimitEnforcement` в provenance. Не считать значение `memoryLimit` гарантией cgroup/Windows job limit.

Для двух машин используйте стабильную проводную сеть; запишите NIC, скорость линка, MTU, маршрут и состояние NTP. Проверка связи предшествует измерению. Разрешайте MQTT-порт только нужному контроллеру, управление — административному адресу. Для одного хоста используйте loopback; для SSH отдельный ключ без хранения его содержимого в артефактах.

Запишите версии брокера, Node/JDK, .NET, PowerShell, ОС, конфигурации и SHA-256 в manifest эксперимента. Состояние governor/boost и фоновые нагрузки фиксируйте перед серией. После подготовки перейдите к [установке брокеров](quick-start.md).

Остановка подготовки не требует остановки службы: до запуска брокера работающих компонентов стенда нет. Измерение завершается по Ctrl+C и правилам [локального runbook](local-stand.md).
