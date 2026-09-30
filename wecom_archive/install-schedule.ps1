$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'
$downloader = Join-Path $PSScriptRoot 'download.py'
$archive = Join-Path $project 'wecom_archive_data\wecom-conversations.zip'
$credential = Join-Path $env:LOCALAPPDATA 'WeComArchive\credentials.json'
$taskName = 'WeCom Conversation Archive Sync'

if (-not (Test-Path -LiteralPath $python)) { throw "Project Python not found: $python" }
if (-not (Test-Path -LiteralPath $credential)) { throw 'Configure local credentials first.' }

$action = New-ScheduledTaskAction -Execute $python -Argument ('"{0}" run --output "{1}"' -f $downloader, $archive) -WorkingDirectory $project
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 45) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$triggers = @((0, 4, 8, 12, 16, 20) | ForEach-Object { New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours($_).AddMinutes(15)) })
$triggers += New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Trigger $triggers -Force | Out-Null
Write-Output 'Scheduled: 00:15, 04:15, 08:15, 12:15, 16:15, 20:15 and at logon.'
