[CmdletBinding()]
param(
    [string]$Configuration = 'Release',
    [string]$Project = 'mqtty/src/EntityFX.MqttSimulator/EntityFX.Mqtty.sln',
    [string]$LogPath = 'MqttBenchmark/artifacts/build/build-output.txt'
)

$ErrorActionPreference = 'Stop'
$targetPath = if ([IO.Path]::IsPathRooted($Project)) { $Project } else { Join-Path $PSScriptRoot $Project }
$buildLogPath = if ([IO.Path]::IsPathRooted($LogPath)) { $LogPath } else { Join-Path $PSScriptRoot $LogPath }
if (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) {
    throw "Build target does not exist: $targetPath"
}
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($buildLogPath)) | Out-Null
Push-Location $PSScriptRoot
try {
    & dotnet build $targetPath --configuration $Configuration --nologo 2>&1 |
        Tee-Object -FilePath $buildLogPath
    $buildExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
Write-Host "BUILD_EXIT_CODE=$buildExitCode"
exit $buildExitCode
