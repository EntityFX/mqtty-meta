# Установка MQTT брокеров без Docker

Цель первого запуска — установить брокер и получить одно сообщение через MQTT. Это проверка доступности, а не измерение производительности. Сначала выполните [подготовку машины](machine-preparation.md). Linux-команды ниже используют Bash; Windows — PowerShell 7. `MQTTY_ROOT` / `$MqttyRoot` обозначает абсолютный путь к mqtty-meta.

## Совместимость и версии

| Брокер | Ubuntu 24.04 | ALT 11.1 p11 | Windows | Управление v3 |
|---|---|---|---|---|
| Aedes 1.1.2, Node >=20 | Подтверждено Node/lockfile; здесь не проверено | Проверено: Node 22.13.1, native preflight | Проверено: Node22.13.1, удалённая native кампания | managed/external, локально/SSH |
| Mosquitto 2.x | Пакет подтверждён поставщиком; здесь не проверено | Проверено: 2.0.15, native preflight | Проверено: 2.1.2, удалённая native кампания | managed/external, локально/SSH |
| ActiveMQ Classic 6.3.2, JDK 17 | Unix-дистрибутив подтверждён Apache; здесь не проверено | Проверено: Java 17, native preflight | Проверено: ZIP6.3.2/JRE17, удалённая native кампания | managed/external, JVM + отдельно pinned launcher |
| EMQX 6.3.0 | Официально поддерживается; здесь не проверено | Проверено: Ubuntu 22.04 portable tar на текущей ALT; официальной поддержки ALT нет | Native Windows не поддерживается поставщиком; Ubuntu WSL2 — предусмотренный маршрут, здесь не проверен | managed/external в Linux, Erlang + pinned launcher |

«Проверено» относится к текущему окружению и коротким функциональным проверкам, не к чистой установке ОС или полной кампании. Подробнее: [отчёт проверок](../../reports/history/setup/platform-validation.md).

Версии ActiveMQ/EMQX соответствуют существующему инвентарю проекта. Mosquitto из репозитория ОС может отличаться от контейнерной 2.1.2: фиксируйте фактическую версию, не смешивайте серии разных версий. В этом комплекте контейнеры не запускаются. Имя каталога `docker/brokers/aedes` — расположение файлов Node.js, само по себе Docker не требуется.

## 1. Клиенты для проверки MQTT

На Ubuntu:

```bash
sudo apt-get install mosquitto-clients
```

На ALT CLI-клиенты поставляет пакет Mosquitto:

```bash
sudo apt-get install mosquitto
command -v mosquitto_sub
command -v mosquitto_pub
```

При отсутствии бинарей проверьте состав `rpm -ql mosquitto`, а не подставляйте имя DEB-пакета. На Windows клиенты находятся в каталоге официальной установки Mosquitto. Эти же клиенты проверяют любой из четырёх брокеров.

После старта выбранного брокера задайте его порт: Aedes `1883`, Mosquitto для отдельного локального примера `1883`, ActiveMQ `3883`, EMQX `4883`. Запускайте один локальный брокер на 1883 одновременно.

В терминале подписчика Linux, из любого каталога:

```bash
MQTT_HOST=127.0.0.1
MQTT_PORT=1883 # заменить порт выбранного брокера
mosquitto_sub -h "$MQTT_HOST" -p "$MQTT_PORT" -V mqttv311 -q 1 -t 'docs/check' -C 1 -W 10
```

Пока подписчик ждёт, в другом терминале:

```bash
MQTT_HOST=127.0.0.1
MQTT_PORT=1883 # тот же порт
mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -V mqttv311 -q 1 -t 'docs/check' -m 'ok'
```

Ожидается `ok`; подписчик завершится после одного сообщения, timeout — ошибка проверки. Для проверок QoS 0/2 повторите обе команды с одинаковым `-q 0` / `-q 2`. При ручной отмене — Ctrl+C.

На Windows в двух окнах PowerShell:

```powershell
$MqttHost = '127.0.0.1'
$MqttPort = 1883 # заменить
& 'C:/Program Files/mosquitto/mosquitto_sub.exe' -h $MqttHost -p $MqttPort -V mqttv311 -q 1 -t docs/check -C 1 -W 10
```

```powershell
$MqttHost = '127.0.0.1'
$MqttPort = 1883
& 'C:/Program Files/mosquitto/mosquitto_pub.exe' -h $MqttHost -p $MqttPort -V mqttv311 -q 1 -t docs/check -m ok
```

## 2. Aedes

На Ubuntu установите Node >=20: пакет Ubuntu 24.04 может быть старее требования lockfile. Вариант без изменения system Node — официальный архив Node 22.13.1 x64:

```bash
mkdir -p "$HOME/opt/mqtty-brokers"
cd "$HOME/opt/mqtty-brokers"
curl -fLO https://nodejs.org/dist/v22.13.1/node-v22.13.1-linux-x64.tar.xz
curl -fLO https://nodejs.org/dist/v22.13.1/SHASUMS256.txt
grep ' node-v22.13.1-linux-x64.tar.xz$' SHASUMS256.txt > node.sha256
sha256sum -c node.sha256
tar -xJf node-v22.13.1-linux-x64.tar.xz
export PATH="$HOME/opt/mqtty-brokers/node-v22.13.1-linux-x64/bin:$PATH"
```

Для распаковки нужен `xz-utils` в Ubuntu. На ALT:

```bash
sudo apt-get install node npm
node --version
```

Если версия меньше 20, используйте официальный Linux-архив после проверки совместимости, вместо установки Aedes с игнорированием engine. На текущем ALT установлен Node 22.13.1. Источник: [Node 22.13.1](https://nodejs.org/en/blog/release/v22.13.1).

На обеих Linux, из MqttBenchmark:

```bash
cd "$MQTTY_ROOT/MqttBenchmark"
node --version
npm --version
npm ci --prefix docker/brokers/aedes
node docker/brokers/aedes/server.js 1883
```

Зависимости воспроизводятся из committed lockfile, процесс работает в foreground и слушает 1883. Успех проверьте клиентами из раздела 1; успешный старт не обязан печатать строку. Остановка — Ctrl+C, затем `ss -ltn '( sport = :1883 )'`: listener должен исчезнуть.

На Windows установите официальный Node x64 >=20 с npm, затем:

```powershell
Set-Location "$MqttyRoot/MqttBenchmark"
node --version
npm ci --prefix docker/brokers/aedes
node docker/brokers/aedes/server.js 1883
```

Проектный server.js привязывается к `0.0.0.0`. На однохостовом пилоте ограничьте входящие подключения firewall; на удалённом хосте разрешите контроллеру только MQTT-порт. Для управляемого запуска остановите ручной процесс и перейдите к [локальному стенду](local-stand.md).

## 3. Mosquitto

Ubuntu:

```bash
sudo apt-get install mosquitto mosquitto-clients
```

ALT:

```bash
sudo apt-get install mosquitto
```

Пакет может автоматически запустить системную службу. На выделенной тестовой машине проверьте `systemctl status mosquitto` и `ss -ltn '( sport = :1883 )'`. Если 1883 занят этой службой, остановите её перед запуском отдельного процесса: `sudo systemctl stop mosquitto`. Не останавливайте чужую службу на рабочем сервере; выберите отдельный тестовый порт и согласуйте его во всех настройках.

Из MqttBenchmark:

```bash
cd "$MQTTY_ROOT/MqttBenchmark"
MOSQUITTO_EXE=$(command -v mosquitto || true)
if test -z "$MOSQUITTO_EXE"; then MOSQUITTO_EXE=/usr/sbin/mosquitto; fi
test -x "$MOSQUITTO_EXE"
"$MOSQUITTO_EXE" -h
"$MOSQUITTO_EXE" -c "$PWD/docker/brokers/mosquitto/mosquitto.local.conf"
```

Конфигурация слушает `127.0.0.1:1883`, разрешает анонимное подключение для пилота, выключает persistence и уменьшает период `$SYS` до 2 секунд. Проверьте `ok` из раздела 1. Остановка foreground-процесса — Ctrl+C. Если до проверки вы остановили отдельную системную службу, восстановите её только после освобождения порта: `sudo systemctl start mosquitto`.

Windows: загрузите x64 installer с [официальной страницы](https://mosquitto.org/download/), установите в стандартный каталог. Если установлена и запущена служба Mosquitto, проверьте `Get-Service mosquitto`; для выделенного стенда остановите через `Stop-Service mosquitto` в административном окне. Из обычного PowerShell в MqttBenchmark:

```powershell
Set-Location "$MqttyRoot/MqttBenchmark"
& 'C:/Program Files/mosquitto/mosquitto.exe' -h
& 'C:/Program Files/mosquitto/mosquitto.exe' -c "$PWD/docker/brokers/mosquitto/mosquitto.local.conf"
```

Остановка — Ctrl+C; проверка порта: `Get-NetTCPConnection -LocalPort 1883 -State Listen -ErrorAction SilentlyContinue`. Локальный управляемый профиль описан в [runbook](local-stand.md).

## 4. ActiveMQ Classic

Нужен именно **Classic 6.3.2**, не Artemis. Для одинакового baseline используйте JDK 17. Ubuntu: `sudo apt-get install openjdk-17-jdk`; ALT: `sudo apt-get install java-17-openjdk-devel`. `java -version` должен показать 17; если установлено несколько JDK, выберите нужный JAVA_HOME явно.

Из пользовательского каталога на обеих Linux:

```bash
mkdir -p "$HOME/opt/mqtty-brokers"
cd "$HOME/opt/mqtty-brokers"
curl -fLO https://archive.apache.org/dist/activemq/6.3.2/apache-activemq-6.3.2-bin.tar.gz
curl -fLO https://archive.apache.org/dist/activemq/6.3.2/apache-activemq-6.3.2-bin.tar.gz.sha512
sha512sum -c apache-activemq-6.3.2-bin.tar.gz.sha512
tar -xzf apache-activemq-6.3.2-bin.tar.gz
cd apache-activemq-6.3.2
java -version
```

До первого запуска замените в `conf/activemq.xml` список transport connectors единственным MQTT connector:

```xml
<transportConnectors>
  <transportConnector name="mqtt" uri="mqtt://127.0.0.1:3883"/>
</transportConnectors>
```

У существующего `<broker>` установите `persistent="false"` и `useJmx="false"`. Для этого пилота отключите импорт Jetty (`<import resource="jetty-spring.xml"/>`), если он включён: консоль не нужна для проверки MQTT. Остальную структуру штатного XML сохраните. Хэш готового файла записывается перед серией.

Из каталога дистрибутива:

```bash
export JAVA_HOME=$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")
sha256sum conf/activemq.xml
bin/activemq console
```

Listener должен появиться на `127.0.0.1:3883`. Проверьте MQTT из раздела 1 с этим портом; остановите Ctrl+C. Для daemon-варианта из того же каталога используют `bin/activemq start`, затем `bin/activemq stop`; foreground удобнее для диагностики первого запуска.

На Windows установите JDK 17, скачайте и распакуйте ZIP 6.3.2 с [страницы выпуска Apache](https://activemq.apache.org/components/classic/download/classic-06-03-02). Сверьте `Get-FileHash -Algorithm SHA512` с файлом поставщика, внесите те же изменения XML. В каталоге дистрибутива:

```powershell
$env:JAVA_HOME = 'C:/Program Files/Java/jdk-17' # реальный каталог выбранного JDK
& "$env:JAVA_HOME/bin/java.exe" -version
Get-FileHash conf/activemq.xml -Algorithm SHA256
./bin/activemq.bat start
```

Windows bat передаёт команду start в Java console application и остаётся в foreground; Linux launcher использует console. Проверка — 3883, остановка — Ctrl+C. В v3 указывайте executable JVM и отдельно launcherExecutable/launcherSha256: контроллер проверяет хэш слушающего Java PID, а не принимает shell/bat за брокер. Пример spec и генерация находятся в [локальном руководстве](local-stand.md).

## 5. EMQX

Для одинаковой переносимой установки используйте EMQX 6.3.0 x64 из официального portable tar. Ubuntu 24.04 поддерживается поставщиком; на текущей ALT 11.1 успешно проверен Ubuntu 22.04 архив. Это экспериментально проверенная совместимость конкретного архива и окружения, а не официальная поддержка ALT. Установка Ubuntu DEB на ALT не требуется.

На Ubuntu нужны `libssl3`, `libstdc++6`, `libncurses6`, `libtinfo6`; на ALT — соответствующие OpenSSL 3, libstdc++ и ncurses/tinfo. Состав зависимостей проверяется через `ldd` установленного runtime: отсутствие библиотек надо исправить до запуска. [Требования поставщика](https://docs.emqx.com/en/emqx/latest/get-started/deploy/install.html).

В Bash, из пользовательского каталога на Linux:

```bash
mkdir -p "$HOME/opt/mqtty-brokers/emqx"
cd "$HOME/opt/mqtty-brokers"
EMQX_ARCHIVE=emqx-enterprise-6.3.0-ubuntu22.04-amd64.tar.gz
curl -fLO "https://www.emqx.com/en/downloads/enterprise/6.3.0/$EMQX_ARCHIVE"
curl -fLO "https://www.emqx.com/en/downloads/enterprise/6.3.0/$EMQX_ARCHIVE.sha256"
# Файл поставщика содержит digest; сверить именно скачанный архив.
EMQX_EXPECTED_SHA256=$(awk '{print $1; exit}' "$EMQX_ARCHIVE.sha256")
printf '%s  %s\n' "$EMQX_EXPECTED_SHA256" "$EMQX_ARCHIVE" | sha256sum -c -
tar -xzf "$EMQX_ARCHIVE" -C emqx
cd emqx
ldd erts-*/bin/beam.smp
```

Ожидаются совпавший SHA-256, каталоги bin/etc/erts-* и отсутствие `not found` в ldd. В etc/emqx.conf задайте:

```text
node.name = "emqx77@127.0.0.1"
listeners.tcp.default.bind = "127.0.0.1:4883"
dashboard.listeners.http.bind = "127.0.0.1:18083"
```

Имя node должно быть уникальным на хосте; его адрес для этого одноброкерного узла локальный. Management listeners оставьте локальными. Отключите ненужные TLS/WebSocket listeners в активной конфигурации, если они занимают чужие порты. EMQX также использует Erlang distribution/epmd порты; два независимых экземпляра требуют разных имён и конфигураций. Набор файлов etc/base.hocon, etc/emqx.conf, etc/vm.args и существующий data/configs/cluster.hocon включается в configFiles инвентаря. Не переиспользуйте неизвестное состояние data от прежних серий.

Из каталога дистрибутива:

```bash
DEBUG=0 bin/emqx foreground
```

DEBUG=0 предотвращает конфликт с одноимённой переменной окружения запуска .NET. Ожидается listener 4883; выполните MQTT-проверку раздела 1. Остановка foreground — Ctrl+C (в интерактивном Erlang break menu: `a` для abort). Во втором терминале из этого же каталога можно выполнить `DEBUG=0 bin/emqx stop`; проверьте освобождение порта через ss. Daemon-вариант: `DEBUG=0 bin/emqx start`, проверка `DEBUG=0 bin/emqx ctl status`, остановка `DEBUG=0 bin/emqx stop`. Для managed v3 сначала остановите daemon: контроллер сам запускает foreground launcher и останавливает идентифицированный Erlang PID.

Проверьте ограничения лицензии установленной редакции до серии. Ограничение числа соединений не следует трактовать как аппаратный предел производительности. [Настройки listener](https://docs.emqx.com/en/emqx/latest/configuration/listener.html).

**Windows:** EMQX отсутствует в официальной native Windows матрице. На Windows с подтверждённой поддержкой WSL2 (сначала [проверить build](two-machine-stand.md)) установите Ubuntu 24.04: `wsl --install -d Ubuntu-24.04`, завершите первичную настройку пользователя и выполните этот Linux-рецепт внутри WSL. Для одного компьютера брокеры и CLI полного набора запускаются внутри WSL по loopback. Для отдельного контроллера настройте SSH и forwarding по [руководству двух машин](two-machine-stand.md). Остановка — внутри WSL описанными командами. WSL и NAT входят в условия измерений.

## После проверки брокера

Выберите управляемый запуск скриптом либо ручной external-запуск по [локальному руководству](local-stand.md). Все четыре брокера имеют проверяемую identity listener; имя launcher отделено от фактического executable. Существующая короткая проверка всех четырёх на ALT подтверждает работоспособность маршрута, но не заменяет 288 успешных запусков для калибровки.
