$ErrorActionPreference = 'Stop'
$verification = Get-Content -LiteralPath 'C:\projects\mqtty-meta\reports\audits\dissertation\network-main-final-v1\package-verification.json' -Raw | ConvertFrom-Json
if ($verification.status -ne 'verified') { throw 'Final package verification required' }
$finalAcceptance = Get-Content -LiteralPath 'C:\projects\mqtty-meta\reports\audits\dissertation\network-main-final-v1\acceptance.json' -Raw | ConvertFrom-Json
$oldReceipt = Get-Content -LiteralPath 'C:\projects\mqtty-meta\reports\audits\dissertation\network-main-transfer-v1\verification.json' -Raw | ConvertFrom-Json
$articleReceipt = Get-Content -LiteralPath 'C:\projects\mqtty-meta\reports\audits\dissertation\article-1-main-execution-v1\raw-transfer.json' -Raw | ConvertFrom-Json
$targets = @(
 [pscustomobject]@{ path='C:\temp\mqtty-evidence\network-final-20261028\raw-002'; archive='C:\temp\mqtty-evidence\network-final-20261028\network-full-final.tar.gz'; expectedSha=$finalAcceptance.transfer.archive.sha256; expectedCount=4412 },
 [pscustomobject]@{ path='C:\temp\mqtty-evidence\network-full-3h-v1\raw-001'; archive='C:\temp\mqtty-evidence\network-full-3h-v1\network-full-3h-v1.tar.gz'; expectedSha=$oldReceipt.transfer.archiveSha256; expectedCount=2854 },
 [pscustomobject]@{ path='C:\temp\mqtty-evidence\article1-matched-main-v1\raw-001'; archive='C:\temp\mqtty-evidence\article1-matched-main-v1\article1-matched-main-v1-raw-001.tar.gz'; expectedSha=$articleReceipt.archiveSha256; expectedCount=658 }
)
foreach ($target in $targets) {
 $fullTarget = [System.IO.Path]::GetFullPath($target.path)
 if (-not $fullTarget.StartsWith('C:\temp\mqtty-evidence\',[System.StringComparison]::OrdinalIgnoreCase) -or $fullTarget -ne $target.path) { throw 'Target escaped allowed directory' }
 $item = Get-Item -LiteralPath $fullTarget -Force
 $ancestor = $item
 while ($null -ne $ancestor -and $ancestor.FullName.StartsWith('C:\temp',[System.StringComparison]::OrdinalIgnoreCase)) {
  if (($ancestor.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse point in target path' }
  $ancestor = $ancestor.Parent
 }
 $archiveHash = (Get-FileHash -LiteralPath $target.archive -Algorithm SHA256).Hash.ToLowerInvariant()
 if ($archiveHash -ne $target.expectedSha) { throw ('Backup archive SHA mismatch: '+$target.archive) }
 $files = @(Get-ChildItem -LiteralPath $fullTarget -Recurse -File -Force)
 if ($files.Count -ne $target.expectedCount) { throw ('Unexpected files in deletion target: '+$fullTarget) }
 $target | Add-Member -NotePropertyName files -NotePropertyValue $files.Count
 $target | Add-Member -NotePropertyName bytes -NotePropertyValue (($files | Measure-Object -Property Length -Sum).Sum)
 $target | Add-Member -NotePropertyName archiveShaVerified -NotePropertyValue $archiveHash
}
$before = ([System.IO.DriveInfo]::new('C:\')).AvailableFreeSpace
foreach ($target in $targets) { Remove-Item -LiteralPath $target.path -Recurse -Force }
$after = ([System.IO.DriveInfo]::new('C:\')).AvailableFreeSpace
$receipt = [ordered]@{status='completed'; scope='Verified extracted duplicates only; archives retained'; deleted=$targets; bytesOfFilesRemoved=($targets | Measure-Object -Property bytes -Sum).Sum; freeBytesBefore=$before; freeBytesAfter=$after; actualFreeBytesIncrease=($after-$before); importantArchivesRetained=$true; unrelatedTempFilesDeleted=$false; rawRecreation='Extract the retained tar.gz into a new directory before rerunning raw checks'}
$receipt | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath 'C:\projects\mqtty-meta\reports\audits\dissertation\temp-cleanup-20261029\receipt.json' -Encoding utf8
$receipt | ConvertTo-Json -Depth 10
