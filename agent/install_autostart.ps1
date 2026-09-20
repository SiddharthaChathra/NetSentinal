# Keep the NetSentinel agent running on Windows.
#
# The agent only reports while its process is alive, so closing the terminal
# or rebooting stops it — and the device then shows as offline even though the
# machine is fine. This registers a Scheduled Task that starts the agent at
# logon and restarts it if it dies.
#
# Run from the repo root:
#     powershell -ExecutionPolicy Bypass -File agent\install_autostart.ps1
#
# Remove it again with:
#     schtasks /delete /tn "NetSentinel Agent" /f

$ErrorActionPreference = "Stop"

$TaskName = "NetSentinel Agent"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$AgentScript = Join-Path $RepoRoot "agent\agent.py"

if (-not (Test-Path $AgentScript)) {
    Write-Error "Could not find $AgentScript. Run this from the NetSentinel repo."
}

if (-not (Test-Path (Join-Path $RepoRoot ".env"))) {
    Write-Warning "No .env in $RepoRoot - the agent needs API_BASE_URL and AGENT_TOKEN. Get them from the app's Add a device panel."
}

# pythonw.exe runs without a console window, so the agent does not leave a
# terminal open. Prefer the repo's virtualenv if one exists.
$VenvPythonW = Join-Path $RepoRoot ".venv\Scripts\pythonw.exe"
if (Test-Path $VenvPythonW) {
    $PythonW = $VenvPythonW
} else {
    $PythonCmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if (-not $PythonCmd) { $PythonCmd = Get-Command python.exe -ErrorAction SilentlyContinue }
    if (-not $PythonCmd) { Write-Error "No Python found on PATH, and no .venv in the repo." }
    $PythonW = $PythonCmd.Source
}

Write-Host "Repo:   $RepoRoot"
Write-Host "Python: $PythonW"

$action = New-ScheduledTaskAction -Execute $PythonW `
    -Argument "`"$AgentScript`" --start" -WorkingDirectory $RepoRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn

# Restart if it stops, and never let Windows kill it for running too long.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Reports this machine's network telemetry to NetSentinel." -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "Done. '$TaskName' is registered and running." -ForegroundColor Green
Write-Host "The device should show ONLINE in NetSentinel within a minute."
Write-Host ""
Write-Host "  Check it:  schtasks /query /tn `"$TaskName`""
Write-Host "  Stop it:   schtasks /end   /tn `"$TaskName`""
Write-Host "  Remove it: schtasks /delete /tn `"$TaskName`" /f"
