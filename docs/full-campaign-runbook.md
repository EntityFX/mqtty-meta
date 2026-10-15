# Развёртывание и полный прогон MqttBenchmark / MqttY

Пошаговый маршрут для **новой машины и новой серии**, без адресов, имён пользователей,
номеров дисков и CPU текущего стенда. Команды выполняет оператор; этот документ
сам по себе не устанавливает ПО и не меняет работающую кампанию.

Основной вариант: все четыре native-брокера, контроллер, симулятор и отчёты на
одной Linux-машине. Windows используется для SSH и просмотра результатов либо
запускает этот Linux внутри WSL2/VM. Виртуализация и loopback входят в условия
измерения: результаты разных размещений не смешиваются.

## Что получится

Порядок: **Aedes → Mosquitto → ActiveMQ Classic → EMQX → смешанная MqttRelay-сеть**.
Для каждого брокера целиком выполняются:

| Этап | Покрытие | Примечание |
|---|---:|---|
| Pilot | 3 | QoS 0/1/2, 128 publishers; отдельно от основной матрицы |
| Натурная калибровка | 72 | 24 точки × 3 повтора |
| Профиль двойника | 1 | Только из калибровки этого брокера |
| BrokerFidelity | 72 | Калибровочное воспроизведение |
| Независимые натурные измерения | 72 | Новый порядок запусков; профиль не меняется |
| Контрольные симуляции | 72 | Сравнение с независимыми observations |
| Однородная MqttRelay-сеть | 72 | Четыре двойника одного брокера |
| Markdown / автономный HTML | 1 пара | Таблицы, графики, CSV/JSON, provenance |

После четырёх брокеров — 72 смешанные сетевые симуляции. Итого **576 натурных +
576 BrokerFidelity + 360 MqttRelay = 1512 основных запусков**, без pilots и retries.

Матрица: payload 16/256 байт × QoS 0/1/2 × publishers 1/16/64/128 × три повтора.
Натурные длительности 5/30/5 секунд, drain до 30 секунд. Ускоренный вариант:
симуляции 5/10 секунд, drain до 30 секунд, tick 0,1 мс, до четырёх независимых
процессов. Внутри каждого процесса refresh последовательный. Натурные измерения
с симуляциями не пересекаются. Для исходного 30-секундного модельного окна есть
параметр `--simulation-seconds 30`.

## 0. Выбрать поддерживаемую среду

| Среда | Маршрут |
|---|---|
| Ubuntu 24.04 x86-64 | Основные команды ниже; systemd, пользовательский D-Bus |
| ALT Linux 11.1 | Тот же сценарий после установки RPM-зависимостей; см. подготовку ОС |
| Windows | SSH к Linux либо Ubuntu WSL2/VM и все команды внутри Linux |
| macOS, другая ОС | Linux VM с подходящими дистрибутивами брокеров |
| ARM64 | Нужны соответствующие SDK/runtime/Node/EMQX; приведённые x64-архивы не подходят |

Под «любой машиной» здесь понимается новая машина с совместимым Linux/runtime,
а не возможность запустить четыре брокера на произвольной ОС/архитектуре.
Native Windows-полный набор не предлагается: EMQX требует Linux-маршрута.
Новые переносимые команды рассчитаны на Linux; Windows-контроллер с отдельным
Linux-брокером описан в [двухмашинном руководстве](../MqttBenchmark/docs/two-machine-stand.md).

Рекомендуется **16 GiB RAM**, минимум два физических ядра, для четырёх процессов
желательно не менее четырёх доступных контроллеру ядер. Готовый профиль лимитов:
брокер 4 GiB, контроллер с дочерними процессами 8 GiB. На машине с меньшей памятью
нужна отдельная валидация и другой ресурсный профиль; один параметр workers не
меняет эти лимиты. Брокерный CPU в текущем контракте `singleCorePinned` — 0..63.

Под evidence нужен отдельный **смонтированный ext4** раздел. Для планирования
заложите 150–200 GiB свободного места; это запас, не точный размер кампании.
Уточните потребление после pilot. Стартовый минимум скрипта — 20 GiB на evidence
и 10 GiB на системном разделе. Он не является оценкой объёма полного покрытия.

Результаты текущей ALT-машины подтверждают работу базового маршрута. Чистая
установка Ubuntu/WSL2 в рамках подготовки этого документа не выполнялась.
Переносимые обвязки проверены тестами с другой раскладкой CPU и другими путями;
реальный preflight и pilot обязательны на целевой машине.

## 1. Установить системные зависимости

Ниже Bash в Ubuntu 24.04. Для ALT используйте соответствующий раздел
[machine-preparation.md](../MqttBenchmark/docs/machine-preparation.md), а не DEB-команды.

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates tar gzip unzip xz-utils \
  python3 python3-venv python3-pip python3-dbus iproute2 procps \
  openjdk-17-jdk mosquitto mosquitto-clients \
  libicu74 libssl3t64 zlib1g libgssapi-krb5-2 libstdc++6 libncurses6 libtinfo6

python3 --version
/bin/python3 -c 'import dbus; print("system dbus-python: OK")'
test -f /sys/fs/cgroup/cgroup.controllers
systemctl --user is-system-running
```

Python для обвязки должен быть **3.11+**; Ubuntu 24.04 предоставляет 3.12.
Требуется **cgroup v2**: отсутствие `cgroup.controllers` означает, что готовый
контракт проверки MemoryMax/cgroup этой установкой не выполнен.
Для D-Bus launcher важен именно `/bin/python3` с `dbus-python`, а не только venv.
Статус systemd `degraded` требует изучения конкретных unit; отсутствие user bus
блокирует запуск. Не запускайте контроллер через `sudo python`: меняются user bus,
домашний каталог и владелец артефактов.

Для WSL2 сначала проверьте `ps -p 1 -o comm=`. Если systemd отсутствует,
включите его по [инструкции Microsoft](https://learn.microsoft.com/en-us/windows/wsl/systemd).
Завершение WSL/VM или выключение хоста останавливает эксперимент; user service
не делает гостевую машину независимой от Windows-хоста.

Чтобы user service работал после выхода из SSH:

```bash
loginctl enable-linger "$USER"
loginctl show-user "$USER" -p Linger
```

Ожидается `Linger=yes`. Если ОС требует административные права, настройте это
до серии. Наличие linger не означает автозапуск transient unit после перезагрузки:
после перезагрузки используется resume из шага 12.

PowerShell 7 установите по [официальному рецепту Microsoft](https://learn.microsoft.com/en-us/powershell/scripting/install/install-ubuntu):

```bash
curl -fL https://packages.microsoft.com/config/ubuntu/24.04/packages-microsoft-prod.deb \
  -o /tmp/mqtty-packages-microsoft-prod.deb
sudo dpkg -i /tmp/mqtty-packages-microsoft-prod.deb
sudo apt-get update
sudo apt-get install -y powershell
pwsh -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'
```

Фактические версии пакетов фиксируются при подготовке. Обновления во время
измерений не выполняйте.

## 2. Настроить каталоги и проверить диск

Замените **только значение mount** на своё. Раздел должен быть уже смонтирован;
здесь нет форматирования или удаления существующих данных.

Если существующий ext4-раздел ещё не смонтирован, найдите его UUID через
`lsblk -f`, укажите свой UUID и смонтируйте:

```bash
lsblk -f
export MQTT_EVIDENCE_MOUNT=/mnt/mqtt-evidence
export EVIDENCE_UUID='replace-with-existing-ext4-uuid'
sudo mkdir -p "$MQTT_EVIDENCE_MOUNT"
sudo mount "/dev/disk/by-uuid/$EVIDENCE_UUID" "$MQTT_EVIDENCE_MOUNT"
```

Если раздел уже смонтирован, повторный `mount` не нужен. Здесь намеренно нет
`mkfs`: пустой или новый диск сначала готовится отдельно от исследовательских данных.

```bash
export MQTT_EVIDENCE_MOUNT=/mnt/mqtt-evidence

python3 - "$MQTT_EVIDENCE_MOUNT" <<'PY'
import pathlib, subprocess, sys
p = pathlib.Path(sys.argv[1]).resolve()
actual = subprocess.check_output(['findmnt','-n','-o','TARGET,SOURCE,FSTYPE','-T',str(p)], text=True).split()
system = subprocess.check_output(['findmnt','-n','-o','SOURCE','-T','/'], text=True).strip()
assert len(actual) == 3 and actual[0] == str(p) and actual[2] == 'ext4', actual
assert str(p) != '/' and actual[1].split('[',1)[0] != system.split('[',1)[0], 'Evidence must not be the system filesystem'
print('Evidence mount:', actual)
PY
```

После успешной проверки создайте **свой выделенный каталог** на этом разделе.
Если раздел принадлежит root и каталог ещё не существует:

```bash
sudo install -d -o "$(id -u)" -g "$(id -g)" "$MQTT_EVIDENCE_MOUNT/mqtty-evidence"
```

Не меняйте владельца чужого каталога. Если `mqtty-evidence` уже занят другим
стендом/оператором, используйте отдельный раздел/развёртывание.

```bash
export MQTTY_DATA="$MQTT_EVIDENCE_MOUNT/mqtty-evidence"
export MQTTY_TOOLS="$MQTTY_DATA/tools"
export MQTTY_ROOT="$MQTTY_DATA/sources/mqtty-meta"
export SERIES_ID="full-$(date -u +%Y%m%d-%H%M%S)"
export MQTTY_SERIES="$MQTTY_DATA/$SERIES_ID"
export BROKER_ROOT="$MQTTY_SERIES/brokers-native"

mkdir -p "$MQTTY_TOOLS/tmp" "$MQTTY_TOOLS/downloads" "$MQTTY_DATA/sources" "$BROKER_ROOT"
export TMPDIR="$MQTTY_TOOLS/tmp"
export TMP="$TMPDIR"
export TEMP="$TMPDIR"
export PIP_CACHE_DIR="$MQTTY_TOOLS/pip-cache"
export NUGET_PACKAGES="$MQTTY_TOOLS/nuget-packages"
export DOTNET_CLI_HOME="$MQTTY_TOOLS/dotnet-home"
export MPLCONFIGDIR="$MQTTY_TOOLS/matplotlib-cache"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_NOLOGO=1

df -h / "$MQTT_EVIDENCE_MOUNT"
```

Сохраните эти переменные в своём `paths.sh` и выполняйте `source /path/to/paths.sh`
в новых SSH-сеансах. Не пересоздавайте `SERIES_ID` при resume. TMPDIR должен
оставаться коротким: слишком длинные пути мешают Unix diagnostic sockets .NET.
Не перезаписывайте tools/bin или distributions уже работающей серии.

## 3. Перенести точные исходники

Нужны три Git checkout: метапроект с `tools/campaign`, `MqttBenchmark`, `mqtty`.
Скрипты этого руководства могут ещё отсутствовать в опубликованной удалённой
ветке. Надёжный способ перенести текущую локальную версию — Git bundles.

На исходной Windows-машине, из корня `mqtty-meta`, PowerShell 7:

```powershell
$Transfer = 'E:/mqtty-transfer' # свой диск/каталог
New-Item -ItemType Directory -Force $Transfer | Out-Null
git status --short
git -C MqttBenchmark status --short
git -C mqtty status --short
git bundle create "$Transfer/mqtty-meta.bundle" HEAD
git -C MqttBenchmark bundle create "$Transfer/MqttBenchmark.bundle" HEAD
git -C mqtty bundle create "$Transfer/mqtty.bundle" HEAD
Get-ChildItem "$Transfer/*.bundle" | Get-FileHash -Algorithm SHA256
```

Нужные изменения должны быть закоммичены: `bundle HEAD` не переносит рабочие
незакоммиченные файлы. Передайте три bundle и их SHA-256 на новую машину через
scp/носитель. На целевой Linux проверьте хэши, затем:

```bash
export BUNDLE_DIR=/absolute/path/to/transferred-bundles
sha256sum "$BUNDLE_DIR/mqtty-meta.bundle" "$BUNDLE_DIR/MqttBenchmark.bundle" "$BUNDLE_DIR/mqtty.bundle"

git clone "$BUNDLE_DIR/mqtty-meta.bundle" "$MQTTY_ROOT"
git clone "$BUNDLE_DIR/MqttBenchmark.bundle" "$MQTTY_ROOT/MqttBenchmark"
git clone "$BUNDLE_DIR/mqtty.bundle" "$MQTTY_ROOT/mqtty"

git -C "$MQTTY_ROOT" rev-parse HEAD
git -C "$MQTTY_ROOT/MqttBenchmark" rev-parse HEAD
git -C "$MQTTY_ROOT/mqtty" rev-parse HEAD
test -f "$MQTTY_ROOT/tools/campaign/portable-linux.py"
test -f "$MQTTY_ROOT/tools/campaign/accelerated-resume.py"
```

Для публичной опубликованной версии допустим `git clone --recurse-submodules`,
но проверьте наличие новых скриптов и **точные commits**, а не только имя ветки.
Проверенный измерительный код текущей серии: MqttBenchmark `3a68946`, MqttY
`0fc65a2`. Новая серия фиксирует фактические SHA обоих checkout и метапроекта.

Не используйте старые `prepare.py`, `prepare-linux.py`, `prepare-linux-controller.py`
без адаптации: они привязаны к прежнему hd250, адресу и раскладке CPU. Ниже
используется новый [portable-linux.py](../tools/campaign/portable-linux.py).

## 4. Подготовить .NET и Python

CLI имеют target `net6.0`; установка только runtime 8/10 его не заменяет.
Для воспроизведения используем SDK 6.0.428 с runtime 6.0.36. .NET 6 завершил
поддержку Microsoft 12 ноября 2024 года; это историческая зависимость эксперимента,
а не рекомендация для нового production-приложения.
[Источник Microsoft](https://devblogs.microsoft.com/dotnet/dotnet-6-end-of-support/).

```bash
curl -fL https://dot.net/v1/dotnet-install.sh -o "$MQTTY_TOOLS/downloads/dotnet-install.sh"
bash "$MQTTY_TOOLS/downloads/dotnet-install.sh" \
  --version 6.0.428 --install-dir "$MQTTY_TOOLS/dotnet"
export DOTNET_ROOT="$MQTTY_TOOLS/dotnet"
export PATH="$DOTNET_ROOT:$PATH"
dotnet --list-sdks
dotnet --list-runtimes

python3 -m venv "$MQTTY_TOOLS/campaign-venv"
export PYTHON="$MQTTY_TOOLS/campaign-venv/bin/python"
"$PYTHON" -m pip install -r "$MQTTY_ROOT/MqttBenchmark/scripts/campaign/requirements.txt"
"$PYTHON" -m pip freeze
```

Ожидаются `Microsoft.NETCore.App 6.0.x`, Python 3.11+ и установленные matplotlib /
Markdown. Официальный способ пользовательской установки .NET:
[dotnet-install](https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-install-script).
Пакеты отчётов закреплены в requirements; фактический `pip freeze` сохраняется.

## 5. Собрать две CLI

Публикации помещайте в новые каталоги, не поверх CLI работающей кампании.

```bash
export BENCHMARK_BIN="$MQTTY_TOOLS/controller/bin"
export SIMULATOR_BIN="$MQTTY_TOOLS/simulator"
test ! -e "$BENCHMARK_BIN/EntityFX.MqttBenchmark.Cli.dll"
test ! -e "$SIMULATOR_BIN/EntityFX.MqttY.MqttRelay.App.dll"

dotnet publish "$MQTTY_ROOT/MqttBenchmark/src/EntityFX.MqttBenchmark.Cli/EntityFX.MqttBenchmark.Cli.csproj" \
  -c Release -o "$BENCHMARK_BIN"
dotnet publish "$MQTTY_ROOT/mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.MqttRelay.App/EntityFX.MqttY.MqttRelay.App.csproj" \
  -c Release -o "$SIMULATOR_BIN"

export BENCHMARK_CLI="$BENCHMARK_BIN/EntityFX.MqttBenchmark.Cli.dll"
export SIMULATOR_CLI="$SIMULATOR_BIN/EntityFX.MqttY.MqttRelay.App.dll"
dotnet "$BENCHMARK_CLI" --help
dotnet "$SIMULATOR_CLI" --help
```

При переносе готовых publish-каталогов копируйте **все** DLL, dependencies и
runtimeconfig, а также чистые исходные checkout точных commits сборки.
Одного `.dll` для provenance и запуска недостаточно.

## 6. Развернуть четыре брокера в отдельные каталоги серии

Расширенные рецепты и первичная MQTT-проверка:
[quick-start.md](../MqttBenchmark/docs/quick-start.md).
Все изменения конфигурации ниже завершаются **до prepare и измерений**.

### 6.1. Aedes и Node

Для Linux x64 можно использовать закреплённый Node 22.13.1:

```bash
mkdir -p "$MQTTY_TOOLS/downloads/node" "$MQTTY_TOOLS/node" "$BROKER_ROOT/aedes"
cd "$MQTTY_TOOLS/downloads/node"
curl -fLO https://nodejs.org/dist/v22.13.1/node-v22.13.1-linux-x64.tar.xz
curl -fLO https://nodejs.org/dist/v22.13.1/SHASUMS256.txt
sha256sum --ignore-missing -c SHASUMS256.txt
tar -xJf node-v22.13.1-linux-x64.tar.xz -C "$MQTTY_TOOLS/node" --strip-components=1
export PATH="$MQTTY_TOOLS/node/bin:$PATH"
node --version
npm --version
cp -a "$MQTTY_ROOT/MqttBenchmark/docker/brokers/aedes/." "$BROKER_ROOT/aedes/"
npm ci --prefix "$BROKER_ROOT/aedes" --cache "$MQTTY_TOOLS/npm-cache"
node -p "require('$BROKER_ROOT/aedes/node_modules/aedes/package.json').version"
```

Ожидается Aedes 1.1.2. `npm ci` использует проектный lockfile; не запускайте
`npm update` и не отключайте проверку engine. На другой архитектуре нужен другой
официальный архив Node с проверкой его checksum.
Проектный `server.js` слушает `0.0.0.0`; URI `127.0.0.1` задаёт путь клиента,
а не ограничивает listener. Для общего хоста настройте доступ или измените
только стендовую копию `server.js` до prepare и её последующего хэширования.

### 6.2. Mosquitto

Используется установленный системный binary; фактическая версия будет записана
в spec, даже если отличается от 2.0.15 прежнего ALT-стенда.

```bash
mosquitto -h
cat > "$BROKER_ROOT/mosquitto.conf" <<'CONF'
listener 2883 127.0.0.1
allow_anonymous true
persistence false
log_dest none
sys_interval 2
CONF
```

### 6.3. ActiveMQ Classic

Загрузите 6.3.2 и опубликованный SHA-512 с
[официальной страницы выпуска](https://activemq.apache.org/components/classic/download/classic-06-03-02).
Пример архива: `apache-activemq-6.3.2-bin.tar.gz`. Сверьте checksum до распаковки.

```bash
export CLASSIC_ARCHIVE=/absolute/path/to/apache-activemq-6.3.2-bin.tar.gz
export CLASSIC_CHECKSUM=/absolute/path/to/apache-activemq-6.3.2-bin.tar.gz.sha512
"$PYTHON" - "$CLASSIC_ARCHIVE" "$CLASSIC_CHECKSUM" <<'PY'
import hashlib, pathlib, re, sys
archive, checksum = map(pathlib.Path, sys.argv[1:])
expected = re.search(r'\b[0-9a-fA-F]{128}\b', checksum.read_text()).group().lower()
with archive.open('rb') as stream:
    actual = hashlib.file_digest(stream, 'sha512').hexdigest()
assert actual == expected, (actual, expected)
print('ActiveMQ SHA-512: OK')
PY
mkdir -p "$BROKER_ROOT/activemq"
tar -xzf "$CLASSIC_ARCHIVE" -C "$BROKER_ROOT/activemq"
export CLASSIC_ROOT="$BROKER_ROOT/activemq/apache-activemq-6.3.2"
export JAVA_HOME="$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")"
"$JAVA_HOME/bin/java" -version
```

В `$CLASSIC_ROOT/conf/activemq.xml` сохраните штатный XML и задайте:

- у `<broker>`: `persistent="false"`;
- MQTT connector URI: `mqtt://127.0.0.1:3883`;
- отключите ненужные connectors/Jetty, способные занять чужие порты.

Не заменяйте весь файл XML одним фрагментом. Используется JVM как executable и
отдельный launcher `bin/activemq`; это разные pinned identities.

### 6.4. EMQX

Выберите **версию 6.3.0, tar-архив для своей ОС/архитектуры** на
[странице поставщика](https://www.emqx.com/en/downloads-and-install/enterprise).
Не подменяйте её автоматически `latest`. Скачайте checksum рядом с выбранным
архивом. Пример имени для другой проверенной установки:
`emqx-enterprise-6.3.0-ubuntu22.04-amd64.tar.gz`; это не универсальный binary
для всех Linux. Соответствие вашей ОС проверьте по
[матрице поставщика](https://docs.emqx.com/en/emqx/latest/get-started/deploy/install.html).

```bash
export EMQX_ARCHIVE=/absolute/path/to/your-emqx-6.3.0.tar.gz
export EMQX_CHECKSUM=/absolute/path/to/your-emqx-6.3.0.tar.gz.sha256
"$PYTHON" - "$EMQX_ARCHIVE" "$EMQX_CHECKSUM" <<'PY'
import hashlib, pathlib, re, sys
archive, checksum = map(pathlib.Path, sys.argv[1:])
expected = re.search(r'\b[0-9a-fA-F]{64}\b', checksum.read_text()).group().lower()
with archive.open('rb') as stream:
    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
assert actual == expected, (actual, expected)
print('EMQX SHA-256: OK')
PY
mkdir -p "$BROKER_ROOT/emqx"
tar -xzf "$EMQX_ARCHIVE" -C "$BROKER_ROOT/emqx"
export EMQX_ROOT="$BROKER_ROOT/emqx"
test -x "$EMQX_ROOT/bin/emqx"
ldd "$EMQX_ROOT"/erts-*/bin/beam.smp
```

Если архив содержит дополнительный верхний каталог, поправьте `EMQX_ROOT` на
него. В `ldd` не должно остаться `not found`. В `etc/emqx.conf` задайте уникальное
имя узла и локальные listeners:

```text
node.name = "mqttybench@127.0.0.1"
listeners.tcp.default.bind = "127.0.0.1:4883"
dashboard.listeners.http.bind = "127.0.0.1:18083"
```

Отключите ненужные listeners штатными параметрами установленной версии. Не
копируйте `data` из прежних запусков. До серии запустите foreground, проверьте
MQTT и `DEBUG=0 bin/emqx ctl license info`, затем штатно остановите свой экземпляр.
Проверьте возможность **минимум 129 клиентских соединений** и наличие TPS quota:
EMQX 6 может ограничивать как sessions, так и TPS. Ограничение лицензии нельзя
выдавать за аппаратный предел брокера.
[Официальный контракт лицензирования](https://docs.emqx.com/en/emqx/latest/get-started/deploy/license.html).
Не помещайте ключ лицензии или пароли в публичные команды и отчёты.

### 6.5. Проверить, что порты свободны

```bash
ss -ltnp | grep -E ':(1883|2883|3883|4883|18083)\b' || true
```

Перед managed campaign тестовые процессы должны быть остановлены. Если новая
установка Mosquitto автоматически запустила системную службу на 1883, разберитесь
именно с этим своим экземпляром. Не останавливайте чужие службы; при конфликте
на общем сервере используйте выделенный стенд. `true` выше позволяет пустой вывод,
но найденный listener требует проверки, а не игнорирования.

## 7. Описать реальные пути брокеров

Ниже генерируется spec **этой машины**. Он не копирует pins со старого стенда.
Перед выполнением должны быть определены `BROKER_ROOT`, `CLASSIC_ROOT`,
`EMQX_ROOT`, `JAVA_HOME`, PATH с нужными node/mosquitto.

```bash
export BROKER_SPEC="$MQTTY_SERIES/brokers.spec.json"
"$PYTHON" - <<'PY'
import json, os, pathlib, re, shutil, subprocess
base = pathlib.Path(os.environ['BROKER_ROOT']).resolve()
classic = pathlib.Path(os.environ['CLASSIC_ROOT']).resolve()
emqx = pathlib.Path(os.environ['EMQX_ROOT']).resolve()
node = pathlib.Path(shutil.which('node')).resolve()
mosquitto = pathlib.Path(shutil.which('mosquitto')).resolve()
java = pathlib.Path(os.environ['JAVA_HOME']) / 'bin/java'
version = subprocess.run([str(mosquitto), '-h'], capture_output=True, text=True)
mosquitto_version = re.search(r'mosquitto version\s+(\S+)', version.stdout + version.stderr).group(1)
beam = next(emqx.glob('erts-*/bin/beam.smp'))
classic_files = [classic/'conf/activemq.xml']
if (classic/'bin/env').exists(): classic_files.append(classic/'bin/env')
emqx_files = [emqx/'etc/emqx.conf', emqx/'etc/base.hocon', emqx/'etc/vm.args']
cluster = emqx/'data/configs/cluster.hocon'
if cluster.exists(): emqx_files.append(cluster)
brokers = [
  dict(name='Aedes', mqttUri='mqtt://127.0.0.1:1883', executable=str(node),
       args=[str(base/'aedes/server.js'),'1883'], brokerVersion='1.1.2', versionArgs=['--version'],
       configFiles=[str(base/'aedes/server.js'),str(base/'aedes/package-lock.json')]),
  dict(name='Mosquitto', mqttUri='mqtt://127.0.0.1:2883', executable=str(mosquitto),
       args=['-c',str(base/'mosquitto.conf')], brokerVersion=mosquitto_version, versionArgs=['-h'],
       configFiles=[str(base/'mosquitto.conf')]),
  dict(name='ActiveMQ', mqttUri='mqtt://127.0.0.1:3883', executable=str(java),
       launcherExecutable=str(classic/'bin/activemq'), args=['console'], workingDirectory=str(classic),
       brokerVersion='6.3.2', versionArgs=['-version'], configFiles=list(map(str,classic_files)),
       environment={'JAVA_HOME':str(pathlib.Path(os.environ['JAVA_HOME']).resolve())}),
  dict(name='EMQX', mqttUri='mqtt://127.0.0.1:4883', executable=str(beam),
       launcherExecutable=str(emqx/'bin/emqx'), args=['foreground'], workingDirectory=str(emqx),
       brokerVersion='6.3.0', versionArgs=[], configFiles=list(map(str,emqx_files)),
       startTimeoutSeconds=60, environment={'DEBUG':'0'})
]
path = pathlib.Path(os.environ['BROKER_SPEC'])
path.write_text(json.dumps({'brokers':brokers},indent=2), encoding='utf-8')
print(path)
PY
```

Проверьте JSON: все пути должны существовать. Новая редакция/версия брокера —
новая серия и честно записанная версия, а не изменение `brokerVersion` ради
прохождения preflight. Сохраните checksums установочных архивов рядом со spec.

## 8. Выбрать CPU по фактической топологии

Не копируйте CPU `1-11,13-23` и sibling `12` со старого Xeon. Выбор ниже берёт
доступный логический CPU для брокера и исключает **всю его SMT sibling-группу**
из контроллера. Остальные доступные CPU остаются контроллеру.

```bash
"$PYTHON" - "$MQTTY_SERIES/cpu.env" <<'PY'
import os, pathlib, sys
allowed = os.sched_getaffinity(0)
broker = next((cpu for cpu in sorted(allowed) if cpu <= 63), None)
assert broker is not None, 'singleCorePinned requires an available CPU in 0..63'
def expand(text):
    result=set()
    for part in text.strip().split(','):
        bounds=list(map(int,part.split('-')))
        result.update(range(bounds[0],bounds[-1]+1))
    return result
siblings=expand(pathlib.Path(f'/sys/devices/system/cpu/cpu{broker}/topology/thread_siblings_list').read_text())
controller=sorted(allowed-siblings)
assert controller, 'No separate controller CPU remains; use another host/layout'
workers=min(4,len(controller))
pathlib.Path(sys.argv[1]).write_text(
    f'export BROKER_CPU={broker}\nexport CONTROLLER_CPUS={",".join(map(str,controller))}\n'
    f'export SIM_WORKERS={workers}\n')
print('Broker:',broker,'excluded sibling group:',sorted(siblings),'controller:',controller,'workers:',workers)
PY
source "$MQTTY_SERIES/cpu.env"
lscpu -e=CPU,CORE,SOCKET,ONLINE
```

Это не резервирование эксклюзивного ядра от всей ОС. Скрипт не переносит чужие
процессы и не меняет governor/boost. На маленьком CPU либо при ограниченной RAM
начните с `SIM_WORKERS=1` или `2` до prepare; число логических CPU само по себе
не гарантирует такую же степень ускорения.

## 9. Подготовить новую серию

```bash
export STAND_PWSH="$(readlink -f "$(command -v pwsh)")"
"$PYTHON" "$MQTTY_ROOT/tools/campaign/portable-linux.py" prepare \
  --repository "$MQTTY_ROOT" \
  --series "$MQTTY_SERIES" \
  --spec "$BROKER_SPEC" \
  --broker-assets-root "$BROKER_ROOT" \
  --archive-mount "$MQTT_EVIDENCE_MOUNT" \
  --benchmark-cli "$BENCHMARK_CLI" \
  --simulator-cli "$SIMULATOR_CLI" \
  --pwsh "$STAND_PWSH" \
  --broker-cpu "$BROKER_CPU" \
  --controller-cpus "$CONTROLLER_CPUS" \
  --fidelity-workers "$SIM_WORKERS" \
  --relay-workers "$SIM_WORKERS" \
  --simulation-seconds 10 \
  --cpu-guard 99

export CONTROLLER="$MQTTY_SERIES/controller"
cat "$CONTROLLER/series.json"

"$PYTHON" - <<'PY'
import os, pathlib, shlex
names = ['MQTT_EVIDENCE_MOUNT','MQTTY_DATA','MQTTY_TOOLS','MQTTY_ROOT','SERIES_ID',
         'MQTTY_SERIES','BROKER_ROOT','CONTROLLER','PYTHON','DOTNET_ROOT','PATH',
         'TMPDIR','TMP','TEMP','PIP_CACHE_DIR','NUGET_PACKAGES','DOTNET_CLI_HOME',
         'MPLCONFIGDIR','DOTNET_CLI_TELEMETRY_OPTOUT','DOTNET_NOLOGO','JAVA_HOME',
         'BENCHMARK_CLI','SIMULATOR_CLI','BROKER_SPEC','BROKER_CPU','CONTROLLER_CPUS',
         'SIM_WORKERS','STAND_PWSH']
p = pathlib.Path(os.environ['MQTTY_SERIES'])/'paths.sh'
p.write_text(''.join(f'export {name}={shlex.quote(os.environ[name])}\n' for name in names))
p.chmod(0o600)
print('Для следующего SSH-сеанса: source', p)
PY
```

Будут созданы отдельные stand/calibration/validation/pilot JSON для каждого
брокера, environment snapshot, план и начальные транспортные допущения.
SOURCE диска берётся из `findmnt` этой машины, CPU — из аргументов; имён NIC,
IP и `/dev/sdc1` в новом плане нет. Stand pins создаются штатным PowerShell-скриптом.
Входы после этого не редактируются. Для исправления путей до измерений создайте
новую подготовку с новым ID; не подменяйте sealed evidence.

Все профили новой серии калибруются одним и тем же опубликованным simulator CLI
и source checkout. Это **новая серия**, а не побитовое воспроизведение исторического
профиля Aedes, который был создан калибратором `55a87f1`. Не копируйте готовый
исторический профиль в новую серию ради экономии времени.

Для исходной длительности модели замените **до prepare** только
`--simulation-seconds 10` на `--simulation-seconds 30`. При 10 сек хуже устойчивость
оценки p99; длительность будет указана в каждой строке отчёта.

## 10. Выполнить preflight

```bash
"$PYTHON" "$MQTTY_ROOT/tools/campaign/portable-linux.py" launch \
  --series "$CONTROLLER" --mode preflight
```

Команда печатает имя `mqttbenchmark-controller-....service`. Сохранённое имя:

```bash
export CAMPAIGN_UNIT="$("$PYTHON" -c 'import json,os; print(json.load(open(os.environ["CONTROLLER"]+"/controller-service.json"))["unit"])')"
systemctl --user status "$CAMPAIGN_UNIT" --no-pager
cat "$CONTROLLER/state.json"
tail -n 40 "$CONTROLLER/portable-preflight.err.log"
```

Дождитесь `status=preflight-completed`, а не только принятия systemd unit.
Проверяются listener, фактический executable/PID, pinned configs и MQTT QoS.
При ошибке изучите logs/preflight evidence. Не повышайте пороги для маскировки
неверного пути, чужого PID, отсутствующей affinity, лицензии или clock-проблемы.

Опциональная отдельная проверка pilots всех четырёх брокеров:

```bash
"$PYTHON" "$MQTTY_ROOT/tools/campaign/portable-linux.py" launch \
  --series "$CONTROLLER" --mode pilot
```

Дождитесь `pilots-completed`. Это до 12 дополнительных полных 30-секундных нагрузок
с 90-секундной телеметрией; они не калибруют профиль. Если пропустить отдельную
команду pilot, полный runner сам выполнит pilot перед каждым брокером.

## 11. Запустить полный прогон

```bash
"$PYTHON" "$MQTTY_ROOT/tools/campaign/portable-linux.py" launch \
  --series "$CONTROLLER" --mode run
```

Это весь цикл, включая калибровку профилей, независимый контроль, сети, отчёты и
проверяемые архивы. Не запускайте одновременно второй контроллер этой же серии;
launcher отклонит уже активный owned unit. `prepare` повторно не вызывается.

Процесс работает в transient systemd user unit с установленными CPU affinity,
MemoryMax, TMPDIR и отдельным guard sampler. SSH можно закрыть при включённом
linger. Спать/выключать машину во время измерения нельзя, если требуется
непрерывная серия без прерванных попыток.

Сам факт `failed` у fidelity не останавливает матрицу: exit 3 — сохранённый
научный результат. Exit 1/2 или ошибка архивов/ресурсов — техническая остановка.
Формулировка «выполнено 72/72» не означает «все критерии пройдены».

## 12. Смотреть прогресс, остановить и возобновить

Достаточно read-only команд:

```bash
cat "$CONTROLLER/state.json"
export CAMPAIGN_UNIT="$("$PYTHON" -c 'import json,os; print(json.load(open(os.environ["CONTROLLER"]+"/controller-service.json"))["unit"])')"
systemctl --user show "$CAMPAIGN_UNIT" -p ActiveState -p SubState -p MemoryCurrent
tail -n 30 "$CONTROLLER/portable-run.log"
tail -n 30 "$CONTROLLER/portable-run.err.log"
df -h / "$MQTT_EVIDENCE_MOUNT"
```

Структура:

```text
<series>/
  brokers-native/                      отдельные конфигурации/дистрибутивы
  controller/
    series.json, environment.json
    state.json, controller-service.json, commands.ndjson
    inputs/<Broker>-{stand,calibration,validation,pilot}.json
    brokers/<Broker>/
      calibration/, validation/, broker-calibration.v3.json
      simulations/{fidelity-10s,holdout-10s,relay-10s}/index.json
    mixed/simulations/relay-10s/index.json
    reports/{Aedes,Mosquitto,ActiveMQ,EMQX,MqttRelay-Mixed}.{md,html}
  raw/                                 compact attempt descriptors
  evidence/
    attempts/<campaign>/<key>/attempt-XX.zip
    brokers/<Broker>/simulations/        полные модельные run directories
    verification/                       временное восстановление архивов
    reports/                            копии документов
```

При `--simulation-seconds 30` каталоги симуляций называются без `-10s`.
Для каждой ячейки матрицы показывайте `✅завершено/план/❌технические неудачные
попытки`; провалы критериев — отдельно `⚠️failed/проверено`. Незапущенные точки
не считаются ошибками, намеренно прерванные диагностические попытки указываются
отдельно. Ошибка renderer/архива — ошибка этапа, а не потеря MQTT-сообщения.

Для штатной остановки отправьте Ctrl+C-сигнал активным CLI **своей** серии,
оставив контроллеру время дождаться cleanup и сохранить техническую остановку:

```bash
"$PYTHON" - <<'PY'
import json, os, pathlib, signal
root = pathlib.Path(os.environ['CONTROLLER'])
s = json.loads((root/'state.json').read_text())
jobs = s.get('activeSimulations') or ([s['active']] if s.get('active') else [])
assert jobs, 'Нет активной CLI; дождитесь active в state.json и повторите'
group = s['controllerRuntime']['cgroup']
for job in jobs:
    proc = pathlib.Path('/proc')/str(job['pid'])
    try:
        assert proc.stat().st_uid == os.getuid()
        actual = next(line.split(':',2)[2] for line in (proc/'cgroup').read_text().splitlines() if line.startswith('0::'))
        assert actual == group, 'PID больше не принадлежит controller unit'
        os.kill(job['pid'], signal.SIGINT)
        print('SIGINT:', job['tag'], job['pid'])
    except FileNotFoundError:
        print('Уже завершился:', job['pid'])
PY
```

Дождитесь завершения дочерних процессов, сохранения evidence и остановки
контроллера. Незавершённая попытка может быть записана как cancelled/failed;
не обнуляйте её историю. Не применяйте
`pkill dotnet`, `killall java` и аналогичные команды к общему хосту. При вынужденном
завершении unit остаются прерванные попытки, которые требуется сохранить.

После устранения причины или перезагрузки восстановите переменные и выполните:

```bash
source /absolute/path/to/paths.sh
export PYTHON="$MQTTY_TOOLS/campaign-venv/bin/python"
export CONTROLLER="$MQTTY_SERIES/controller"
"$PYTHON" "$MQTTY_ROOT/tools/campaign/portable-linux.py" launch \
  --series "$CONTROLLER" --mode run
```

Launcher всегда передаёт `--resume`. Завершённые ключи и этапы пропускаются,
retry history сохраняется; незавершённый модельный run остаётся evidence,
а повтор получает новый run directory. Не меняйте JSON, профиль, seed, версии
или thresholds внутри существующей серии. Для новой методики нужна новая серия.
Если после перезагрузки `findmnt SOURCE` изменился, возобновление намеренно
откажет, даже если оператор считает диск тем же. Нужна проверенная и записанная
миграция storage identity; обходить guard или молча менять `archiveDevice` нельзя.

## 13. Проверить архивы и отчёты

После завершения полного прогона ожидается `state.status=completed`. Снова
запускать проверку рядом с идущими натурными измерениями не нужно: архивирование
и повторный aggregate уже включены в штатный цикл. Отдельная проверка после
завершения/в согласованное окно:

```bash
"$PYTHON" "$MQTTY_ROOT/tools/campaign/portable-linux.py" launch \
  --series "$CONTROLLER" --mode verify
```

Сверяйте по каждому брокеру `calibration/archive-verification.json` и
`validation/archive-verification.json`: `verified=true`, совпадение scientific
points и campaign identity. Хэш всего restored observations может отличаться из-за
разного input-tree provenance compact/raw; это документируется, а не скрывается.

Основные evidence: attempts ZIP + SHA-256/receipts; observations; immutable
profiles; experiment manifests, summaries, latency histograms, counters;
inputs; environment; commands. Хэши подтверждают целостность, а повторное
вычисление из raw — проверяемость результатов. Не удаляйте единственный архив
после очистки распакованного raw.

HTML автономен для просмотра графиков. Markdown ссылается на `reports/assets/`,
CSV/JSON и `../brokers/`: переносите **компактный controller bundle**, если нужны
рабочие ссылки, а не только один `.md`. Полные raw остаются отдельно на evidence.

Специальный итоговый генератор Aedes для новой серии **не запускайте вслепую**:
`write-aedes-report.py` предназначен для завершённой исторической серии с
30-секундным первым Fidelity и конкретными версиями. Универсальные отчёты
генерирует сам runner. Пример исторического результата:
[Aedes.md](../MqttBenchmark/artifacts/two-machine-20261014-1340-linux-local/reports/Aedes.md).

## 14. Получить отчёты на Windows

SMB не требуется для измерений. Можно скопировать компактный bundle по SSH:

```powershell
$LinuxTarget = 'user@linux-host'                 # свои данные
$LinuxController = '/mnt/mqtt-evidence/mqtty-evidence/full-YYYYMMDD-HHMMSS/controller'
$LocalBundle = 'D:/mqtt-results/full-YYYYMMDD-HHMMSS'
New-Item -ItemType Directory -Force $LocalBundle | Out-Null
scp -r "${LinuxTarget}:${LinuxController}/reports" $LocalBundle
scp -r "${LinuxTarget}:${LinuxController}/brokers" $LocalBundle
scp -r "${LinuxTarget}:${LinuxController}/inputs" $LocalBundle
scp "${LinuxTarget}:${LinuxController}/series.json" $LocalBundle
scp "${LinuxTarget}:${LinuxController}/environment.json" $LocalBundle
scp "${LinuxTarget}:${LinuxController}/archive-registry.json" $LocalBundle
scp "${LinuxTarget}:${LinuxController}/commands.ndjson" $LocalBundle
```

`brokers` внутри controller — компактные JSON/config/index, не большие модельные
выходные каталоги evidence. Для постоянного зеркала после отдельной настройки
доступа применим [watch-linux.py](../MqttBenchmark/scripts/campaign/watch-linux.py).
Пароль не записывается в spec/series JSON или argv: используйте SSH agent/ключ
и проверьте host key обычным SSH до автоматического запуска.

## 15. Место и типовые ошибки

| Симптом | Что делать |
|---|---|
| `Microsoft.NETCore.App 6.0` не найден | Проверить именно runtime 6.x и PATH/DOTNET_ROOT |
| `dbus` не импортируется / нет user bus | Установить dbus-python для `/bin/python3`, проверить user systemd и linger |
| Mount mismatch / source changed | Проверить фактический mount и устройство; не обходить проверку и не чистить raw |
| Свободно меньше порога | Освободить место за пределами единственной evidence-копии, затем resume |
| Чужой listener/PID | Устранить конфликт собственного процесса; не останавливать чужой сервис |
| CPU affinity mismatch | Проверить доступный cpuset shell/systemd manager и SMT; новая корректная подготовка |
| Неправильная версия/hash | Восстановить pinned binary/config; не переписывать seal |
| Guard CPU / sampling gap | Устранить реальную перегрузку/сбой sampler; учитывать отказ и retry |
| EMQX quota | Проверить sessions/TPS лицензии, не трактовать quota как аппаратную производительность |
| `failed` у fidelity | Сохранить результат; не перекалибровывать по holdout, не ослаблять thresholds |
| Слишком медленный relay | Проверить CPU/RSS и workers; изменение протокола делать только отдельной документированной серией |

Перед каждой CLI-командой проверяются mount и свободное место. При архивировании
каждая sealed attempt проверяется до удаления подробных распакованных файлов.
Runtime watchdog отменяет нагрузку при низком остатке места; это предотвращает
переполнение, но не гарантирует завершение кампании на недостаточном диске.
Память процесса контроллера и симуляций ограничена; фактический `memory.current`
контролируется обвязкой, новые процессы не допускаются при приближении к лимиту.

### Параллельные симуляции при ограниченной памяти

Натурные измерения в программе `singleCorePinned` выполняются последовательно:
одновременные брокеры на одном CPU конкурировали бы за ресурс и изменили условия.
Натурные измерения и симуляции также не перекрываются. Параллельны только
независимые процессы симулятора; последовательный refresh внутри модели сохраняется.

При проверенном запуске двух процессов можно задать в `series.json`:

```json
{
  "simulationFidelityWorkers": 2,
  "simulationRelayWorkers": 2,
  "simulationMemoryPolicy": {
    "reservationPerJobBytes": 2147483648,
    "maxWorkingSetBytes": 6442450944,
    "minSystemAvailableBytes": 2147483648
  }
}
```

Эта политика резервирует память для уже запущенных процессов до того, как их
heap успел вырасти. Перед новым процессом проверяется также запас RAM всей машины.
Из `memory.current` вычитается только потенциально освобождаемый `inactive_file`,
за вычетом dirty/writeback pages; рабочая память ограничена 6 ГиБ, жёсткий лимит
cgroup остаётся 8 ГиБ. Это предотвращает завышение оценки из-за файлового кэша
после проверки ZIP. Резерв 2 ГиБ на процесс является оценкой для допуска, а не
гарантированным максимумом: при превышении защитных порогов evidence сохраняется,
новая нагрузка не запускается. История записывается в `simulation-memory.ndjson`.

План читается при старте контроллера. Изменять число процессов следует после
штатной остановки своего контроллера и проверки завершения дочерних процессов,
затем выполнять resume. Сохраните прежние plan/state, SHA-256 обвязки и доказательства
пилота; не подменяйте запущенный файл обвязки. Профили, бинарь модели, научные входы
и существующие индексы успешных запусков при изменении параллелизма сохраняются.

Перед включением используйте отдельный пилот с тяжёлыми конфигурациями и
проверьте память, завершение и совпадение scientific summary/histogram/counters
с последовательным эталоном. Пилоты не входят в покрытие основной серии.

## Контрольный список готового результата

- 576 успешных основных натурных измерений, история всех retries сохранена.
- 576 завершённых BrokerFidelity и 360 завершённых MqttRelay симуляций.
- Профиль каждого брокера не менялся после калибровки и во время holdout.
- Для каждой native-серии есть проверенные ZIP и повторный aggregate.
- В пяти документах явно разделены execution failures и fidelity failures.
- `notApplicable` у QoS 0 latency не превращён в passed или ноль.
- Указаны длительности, CPU/SMT, версии, commits, guard и транспортные допущения.
- MqttRelay не объявлен натурно валидированной сетью на основании одиночных брокеров.
- В опубликованных артефактах нет SSH-паролей, приватных ключей и ключей лицензии.

## Связанные материалы и статус проверки

[Машина](../MqttBenchmark/docs/machine-preparation.md) ·
[Брокеры](../MqttBenchmark/docs/quick-start.md) ·
[Контракт v3](../MqttBenchmark/docs/campaign-v3.md) ·
[Метрики](../MqttBenchmark/docs/metrics.md) ·
[Варианты размещения](../MqttBenchmark/docs/deployment-layouts.md) ·
[Переносимый publish](../MqttBenchmark/DEPLOYMENT.md).

Для новой обвязки проверены: другая раскладка CPU и SMT, другие paths/device,
генерация двух независимых native seeds и полных pilots, запуск payload через
D-Bus launcher, отказ для неподходящего mount. Выполнены тесты оркестрации с
четырьмя процессами, resume, source/config protection и отменой при нехватке
места. Установка системных пакетов и полная серия на новой физической машине
этим набором тестов не заменяются.
