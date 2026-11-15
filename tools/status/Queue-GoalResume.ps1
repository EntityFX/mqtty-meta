param(
    [Parameter(Mandatory)][guid]$ThreadId,
    [Parameter(Mandatory)][string]$Workspace,
    [Parameter(Mandatory)][string]$CodexExe,
    [Parameter(Mandatory)][string]$PromptPath,
    [Parameter(Mandatory)][string]$ReceiptPath,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
foreach ($item in @($Workspace, $CodexExe, $PromptPath)) {
    if (-not (Test-Path -LiteralPath $item)) { throw "Missing scheduled input: $item" }
}
$promptText = [IO.File]::ReadAllText($PromptPath, [Text.Encoding]::UTF8)
if ([string]::IsNullOrWhiteSpace($promptText)) { throw 'Scheduled message is empty' }
if (Test-Path -LiteralPath $ReceiptPath) {
    throw 'A dispatch receipt already exists; inspect it before any repeat delivery'
}
$receipt = [ordered]@{
    status = 'validated'
    threadId = $ThreadId.ToString()
    workspace = $Workspace
    promptSha256 = (Get-FileHash -LiteralPath $PromptPath -Algorithm SHA256).Hash.ToLowerInvariant()
    observedAtUtc = [DateTimeOffset]::UtcNow.ToString('o')
}
if ($ValidateOnly) {
    $receipt | ConvertTo-Json
    exit 0
}

# Claim once before delivery. An uncertain result must not generate a duplicate.
$claim = [IO.File]::Open($ReceiptPath, [IO.FileMode]::CreateNew)
$claim.Dispose()
$receipt.status = 'dispatching'
$receipt | ConvertTo-Json | Set-Content -LiteralPath $ReceiptPath -Encoding utf8
$process = $null
try {
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $CodexExe
    $start.WorkingDirectory = $Workspace
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = [Text.Encoding]::UTF8
    $start.StandardErrorEncoding = [Text.Encoding]::UTF8
    foreach ($argument in @('queue', '--thread', $ThreadId.ToString(), '--message', $promptText)) {
        $start.ArgumentList.Add($argument)
    }
    $process = [Diagnostics.Process]::Start($start)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(45000)) {
        $process.Kill()
        throw 'Queue command timed out; delivery is uncertain, no automatic retry'
    }
    $receipt.exitCode = $process.ExitCode
    $receipt.stdout = $stdout.GetAwaiter().GetResult()
    $receipt.stderr = $stderr.GetAwaiter().GetResult()
    if ($process.ExitCode -ne 0) { throw "Queue command failed: $($process.ExitCode)" }
    $receipt.status = 'queued'
    $receipt.completedAtUtc = [DateTimeOffset]::UtcNow.ToString('o')
    $receipt | ConvertTo-Json | Set-Content -LiteralPath $ReceiptPath -Encoding utf8
} catch {
    $receipt.status = 'failedOrUncertain'
    $receipt.error = $_.Exception.Message
    $receipt | ConvertTo-Json | Set-Content -LiteralPath $ReceiptPath -Encoding utf8
    throw
} finally {
    if ($process) { $process.Dispose() }
}
