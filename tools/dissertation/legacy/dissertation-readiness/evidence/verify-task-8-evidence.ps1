param(
    [string]$WorkspaceRoot = 'C:\projects\EntityFx.Iot',
    [string]$BaselineRoot = 'H:\MqttBenchmark-artifacts\task4-baseline-singlecore-001',
    [string]$VerifyRegistry
)
$ErrorActionPreference = 'Stop'
$roots = @(
    (Join-Path $WorkspaceRoot 'MqttBenchmark/artifacts/task4-preflight-four-brokers-003'),
    (Join-Path $WorkspaceRoot 'MqttBenchmark/campaigns/task4-guarded-pilot-aedes-001'),
    (Join-Path $WorkspaceRoot 'MqttBenchmark/campaigns/task4-guarded-pilot-mosquitto-001'),
    (Join-Path $WorkspaceRoot 'MqttBenchmark/campaigns/task4-guarded-pilot-emqx-001'),
    (Join-Path $WorkspaceRoot 'MqttBenchmark/campaigns/task4-guarded-pilot-activemq-001'),
    $BaselineRoot
)
$entries = foreach ($root in $roots) {
    $full = (Resolve-Path -LiteralPath $root).Path.TrimEnd('\', '/')
    $pairs = [System.Collections.Generic.SortedDictionary[string,string]]::new([StringComparer]::Ordinal)
    $bytes = [long]0
    foreach ($file in Get-ChildItem -LiteralPath $full -Recurse -Force -File) {
        $relative = $file.FullName.Substring($full.Length + 1).Replace('\', '/')
        $pairs.Add($relative, (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant())
        $bytes += $file.Length
    }
    $canonical = [Text.StringBuilder]::new()
    foreach ($pair in $pairs.GetEnumerator()) { [void]$canonical.Append($pair.Key).Append("`t").Append($pair.Value).Append("`n") }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $treeHash = [Convert]::ToHexString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($canonical.ToString()))).ToLowerInvariant() }
    finally { $sha.Dispose() }
    $sealFiles = 0
    $sealMismatches = 0
    foreach ($seal in Get-ChildItem -LiteralPath $full -Recurse -Force -Filter sha256.json -File) {
        $registry = Get-Content -LiteralPath $seal.FullName -Raw | ConvertFrom-Json
        foreach ($pair in $registry.PSObject.Properties) {
            $sealFiles++
            if ((Get-FileHash -LiteralPath (Join-Path $seal.DirectoryName $pair.Name) -Algorithm SHA256).Hash -ne $pair.Value) { $sealMismatches++ }
        }
    }
    $preflight = Test-Path -LiteralPath (Join-Path $full 'preflight.json')
    $results = @(Get-ChildItem -LiteralPath $full -Recurse -Filter result.json -File | ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json })
    if ($preflight) {
        $report = Get-Content -LiteralPath (Join-Path $full 'preflight.json') -Raw | ConvertFrom-Json
        $identity = $report.identity
    } else { $identity = Get-Content -LiteralPath (Join-Path $full 'campaign.json') -Raw | ConvertFrom-Json }
    $attemptDirectories = @(Get-ChildItem -LiteralPath $full -Recurse -Directory -Filter 'attempt-*')
    [ordered]@{
        rootId = Split-Path $full -Leaf
        localPath = $full
        fileCount = $pairs.Count
        totalBytes = $bytes
        treeSha256 = $treeHash
        sealedFileReferences = $sealFiles
        sealMismatches = $sealMismatches
        acceptedAttempts = @($results | Where-Object status -eq success).Count
        failedSealedAttempts = @($results | Where-Object status -eq failed).Count
        unsealedAttempts = @($attemptDirectories | Where-Object { -not (Test-Path -LiteralPath (Join-Path $_.FullName 'sha256.json')) }).Count
        preflightBrokersPassed = $(if ($preflight) { @($report.brokers | Where-Object { $_.protocol.success }).Count } else { $null })
        identity = $identity
    }
}
$result = [ordered]@{
    schemaVersion = 1
    algorithm = 'SHA256(UTF8(concat(ordinal-sort(relative-path-with-forward-slashes + TAB + lowercase-file-SHA256 + LF)))); all regular files including .lock and sha256.json; no BOM'
    baselineAccepted = 0
    baselineRequired = 288
    brokerFidelityCompleted = 0
    brokerFidelityRequired = 288
    mqttRelayCompleted = 0
    mqttRelayRequired = 24
    roots = @($entries)
}
if ($VerifyRegistry) {
    $expected = Get-Content -LiteralPath $VerifyRegistry -Raw | ConvertFrom-Json
    if ($expected.roots.Count -ne $entries.Count) { throw 'Root count mismatch.' }
    foreach ($entry in $entries) {
        $saved = @($expected.roots | Where-Object rootId -eq $entry.rootId)
        if ($saved.Count -ne 1) { throw "Missing/duplicate root: $($entry.rootId)" }
        foreach ($field in 'fileCount','totalBytes','treeSha256','sealedFileReferences','sealMismatches','acceptedAttempts','failedSealedAttempts','unsealedAttempts','preflightBrokersPassed') {
            if ($saved[0].$field -ne $entry[$field]) { throw "Mismatch: $($entry.rootId), $field" }
        }
        foreach ($field in 'campaignId','configSha256','standSha256','deploymentMode','cpuMode','mqttBenchmarkCommitSha') {
            if ($saved[0].identity.$field -ne $entry.identity.$field) { throw "Identity mismatch: $($entry.rootId), $field" }
        }
        if ($entry.sealMismatches -ne 0) { throw "Invalid seals: $($entry.rootId)" }
    }
    'Verified 6 raw roots: tree hashes, file counts, seals, identities and attempt counts match.'
} else { $result | ConvertTo-Json -Depth 8 }
