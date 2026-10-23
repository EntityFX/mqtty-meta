# Переносимый пакет без Docker

Published CLI работает на Linux/Windows с .NET runtime 6.x, PowerShell 7 и установленным брокером. SDK нужен только для сборки. Пакет содержит broker assets и примеры, но не устанавливает Node/JDK/EMQX. [Подготовка каждой ОС](machine-preparation.md), [установка брокеров](quick-start.md).

## Сборка скриптом или руками

Рабочий каталог — корень **чистого** MqttBenchmark, PowerShell 7:

```powershell
./scripts/BuildPortable.ps1 -OutputRoot dist/mqtt-benchmark
```

Выходной каталог должен быть новым или пустым: повторная сборка поверх старого пакета запрещена, чтобы не сохранить удалённые документы и устаревшие assets. Для `-SkipPublish` допустим каталог, содержащий только заранее опубликованный `bin`; остальные части пакета создаются заново.

Ручной эквивалент:

```powershell
dotnet publish src/EntityFX.MqttBenchmark.Cli -c Release -o dist/mqtt-benchmark/bin
New-Item -ItemType Directory -Force dist/mqtt-benchmark/scripts,dist/mqtt-benchmark/config,dist/mqtt-benchmark/docker
Copy-Item scripts/BrokerStand.ps1,scripts/RemoteNativeStand.ps1,scripts/NewNativeStand.ps1,scripts/MergeNativeStands.ps1,scripts/RunNativeCampaign.ps1,scripts/PinNativeExecutable.ps1 dist/mqtt-benchmark/scripts
Copy-Item config/benchmark-campaign.v3.json dist/mqtt-benchmark/config
git ls-files docker/brokers | ForEach-Object {
  $Target = Join-Path dist/mqtt-benchmark $_
  New-Item -ItemType Directory -Force (Split-Path $Target) | Out-Null
  Copy-Item $_ $Target
}
Copy-Item docs dist/mqtt-benchmark -Recurse
Copy-Item DEPLOYMENT.md dist/mqtt-benchmark
```

Ожидаются bin DLL/dependencies/runtimeconfig, scripts, config, docker/brokers, docs. Название docker/brokers — путь assets; Docker не вызывается. Не включайте node_modules, секреты, локальные configs и результаты в стандартный пакет. Для Aedes после переноса выполните npm ci в assets с исходным lockfile.

Framework-dependent DLL запускается через `dotnet`, не как PowerShell script или executable. Стандартный publish без RID переносим между Linux/Windows для текущих managed dependencies; внешние брокеры и их paths/hashes заново готовятся под целевую ОС. Сохраняйте чистый checkout исходников **точного commit сборки** либо trusted build provenance по существующему контракту CLI. Один каталог bin не является Git provenance.

## Перенос и запуск

На машине сборки из dist: `tar -czf mqtt-benchmark.tar.gz mqtt-benchmark`; Linux `sha256sum mqtt-benchmark.tar.gz`, Windows `Get-FileHash mqtt-benchmark.tar.gz -Algorithm SHA256`. Перенесите архив и checksum через scp/файловую систему, сверяйте checksum перед распаковкой. В новом каталоге назначения: `tar -xzf <archive>`; установите runtime/pwsh/брокер и распакуйте чистый source checkout отдельно.

Из корня распакованного пакета в PowerShell 7:

```powershell
$SourceRepo = '/absolute/path/to/clean/MqttBenchmark' # Windows: C:/work/mqtty-meta/MqttBenchmark
./scripts/NewNativeStand.ps1 -BrokerSpecPath config/brokers.spec.local.json `
  -StandPath config/broker-stand.local.json -CampaignPath config/campaign.local.json
dotnet bin/EntityFX.MqttBenchmark.Cli.dll preflight --config config/campaign.local.json `
  --stand config/broker-stand.local.json --output artifacts/preflight-001 `
  --benchmark-repo $SourceRepo --stand-script scripts/BrokerStand.ps1
dotnet bin/EntityFX.MqttBenchmark.Cli.dll matrix --config config/campaign.local.json `
  --stand config/broker-stand.local.json --campaign artifacts/pilot-001 --max-runs 1 `
  --benchmark-repo $SourceRepo --stand-script scripts/BrokerStand.ps1
```

Spec создаётся на машине брокера по [локальному руководству](local-stand.md). `${MqttBenchmarkRoot}` относится к пакету, где лежит scripts; на SSH worker — к заданному remote repositoryRoot. Подставляйте пути реальных target binaries; не копируйте Linux pin для Windows.

Скриптовый эквивалент:

```powershell
./scripts/RunNativeCampaign.ps1 -ConfigPath config/campaign.local.json `
  -StandPath config/broker-stand.local.json -CampaignDirectory artifacts/pilot-002 `
  -BenchmarkRepository $SourceRepo -Pilot
```

Без -Pilot скрипт запускает полную матрицу и aggregate; ручные формы resume/aggregate описаны в [runbook](local-stand.md). SSH stand использует тот же пакет на контроллере и конфигурацию [двух машин](two-machine-stand.md). Перенос готовых raw — по [метрикам](artifact-format.md).

Остановка — Ctrl+C, managed broker cleanup; external останавливает оператор. Conflicting output не перезаписывается. При неверном хэше/порте/clock смотрите диагностику и сохранённые logs, не меняйте sealed artifacts. Межрепозиторные ссылки docs предполагают mqtty-meta; в отдельном пакете руководства MqttY/diss доступны через исходный checkout.
