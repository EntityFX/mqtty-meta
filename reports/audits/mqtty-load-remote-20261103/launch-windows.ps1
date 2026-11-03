$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$jobRoot = 'C:\Users\sysadm\mqtty-load-benchmark-20261103-001'
$process = Start-Process -FilePath 'C:\Users\sysadm\mqtty-load-benchmark-20261103-001\python\python.exe' -ArgumentList @('-u', "$jobRoot\controller\remote_load_campaign.py", '--root', $jobRoot, '--main-wall-limit', '7200', '--pilot-wall-limit', '1800') -WorkingDirectory $jobRoot -WindowStyle Hidden -RedirectStandardOutput "$jobRoot\remote-stdout.log" -RedirectStandardError "$jobRoot\remote-stderr.log" -Wait -PassThru
exit $process.ExitCode
