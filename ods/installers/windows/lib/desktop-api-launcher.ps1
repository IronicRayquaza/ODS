# ============================================================================
# ODS Windows Desktop -- dashboard-api launcher
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Start, prove and stop the Windows Desktop dashboard-api. The
#          sign-in task ODSDesktopApi-<SID> runs launch-api.ps1, which calls
#          Invoke-ODSDesktopApi: it stops any process it can prove a previous
#          launch left, starts the pinned pythonw.exe on 127.0.0.1, records
#          the process, waits for /health and then for the process to exit.
# Expects: native-llama-runtime.ps1 (Invoke-ODSNativeLlamaHttp,
#          Get-ODSPortalOwnedProcessTree, Stop-ODSPortalOwnedProcesses,
#          Write-ODSPortalProcessOwnership, Read-ODSNativeLlamaJson,
#          Test-ODSNativeLlamaWanted), private-file.ps1, desktop-python.ps1
#          (Test-ODSDesktopPythonInstall), desktop-runtime.ps1
#          (ConvertFrom-ODSDesktopEnvText), desktop-portal.ps1
#          (Get-ODSDesktopPortalPaths, Get-ODSDesktopPortalTaskName). setup
#          copies all of them beside launch-api.ps1, so the task never runs
#          from a checkout.
# Provides: Get-ODSDesktopApiOwnedTree, Stop-ODSDesktopApi,
#           New-ODSDesktopApiArguments, Test-ODSDesktopModelNeedsRestart,
#           Repair-ODSDesktopModelTask, Test-ODSDesktopPortalNeedsRestart,
#           Repair-ODSDesktopPortalTask, Invoke-ODSDesktopApi
# Modder notes: a recorded PID is trusted only while its executable path and
#   start time still match; Windows reuses PIDs, and a reused one belongs to
#   someone else. pythonw.exe needs stdout and stderr redirected: without a
#   console uvicorn otherwise exits silently.
# ============================================================================

$script:ODSDesktopApiStartupSeconds = 90
# main's model task runs powershell.exe from the task (main checks that
# command line, so Desktop cannot route it through conhost --headless like its
# own task). With Windows Terminal as the default terminal that task opens a
# window at sign-in, and closing it ends the task with STATUS_CONTROL_C_EXIT.
# While the app runs, the launcher restarts the model task after exactly that
# exit, never after a stop main's model controller made.
$script:ODSDesktopConsoleClosedExit = [uint32]3221225786   # 0xC000013A
$script:ODSDesktopModelWatchSeconds = 30
$script:ODSDesktopModelRestartLimit = 5
# Task Scheduler does not restart a task that exits with an error: RestartCount
# covers only a failed launch (a probe task that exits 1 ran once in 150 s,
# 2026-11-05). Without this, a slow first start after sign-in or a crash of the
# gateway, ingress or edge leaves Portal down until the next sign-in.
$script:ODSDesktopPortalRestartLimit = 5
$script:ODSDesktopTaskNeverRan = [uint32]0x41303   # SCHED_S_TASK_HAS_NOT_RUN

function Get-ODSDesktopApiOwnedTree([string]$OwnershipPath) {
    <#
    .SYNOPSIS
        The live process tree a previous launch recorded, or $null when the
        record is absent or every recorded process is gone or was reused.
    #>
    if (-not (Test-Path -LiteralPath $OwnershipPath -PathType Leaf)) { return $null }
    $record = Read-ODSNativeLlamaJson $OwnershipPath
    $roots = @(foreach ($entry in @($record.Processes)) {
        if ($entry.ExitedAt) { continue }
        # As main reads it: PowerShell 7 already parsed it, 5.1 casts the text.
        $started = [datetime]$entry.StartedAt
        $process = Get-Process -Id ([int]$entry.ProcessId) -ErrorAction SilentlyContinue
        if (-not $process) { continue }
        try {
            $same = ([string]$process.Path).Equals([string]$entry.ExecutablePath, [StringComparison]::OrdinalIgnoreCase) -and
                [math]::Abs(($process.StartTime.ToUniversalTime() - $started.ToUniversalTime()).TotalMilliseconds) -lt 1
        } finally { $process.Dispose() }
        if (-not $same) { continue }
        [pscustomobject]@{ ProcessId = [int]$entry.ProcessId; CreationDate = $started
            ExecutablePath = [string]$entry.ExecutablePath; ExitedAt = $null }
    })
    if (-not $roots.Count) { return $null }
    return (Get-ODSPortalOwnedProcessTree $roots @(Get-CimInstance Win32_Process -ErrorAction Stop))
}

function Stop-ODSDesktopApi([string]$OwnershipPath) {
    # Stops only what the record proves; returns how many processes it stopped.
    $owned = Get-ODSDesktopApiOwnedTree $OwnershipPath
    $count = 0
    if ($owned) {
        try {
            Stop-ODSPortalOwnedProcesses $owned.Handles
            $count = $owned.Handles.Count
        } finally {
            foreach ($process in $owned.Handles) { $process.Dispose() }
        }
    }
    if (Test-Path -LiteralPath $OwnershipPath) { Remove-Item -LiteralPath $OwnershipPath -Force }
    return $count
}

function New-ODSDesktopApiArguments([string]$AppSource, [int]$Port) {
    # --app-dir: the ._pth file puts Python in isolated mode, so the working
    # directory is not on sys.path.
    if ($AppSource -match '["\r\n]' -or $AppSource.EndsWith('\')) { throw 'The ODS app folder path cannot be passed to Python.' }
    return ('-m uvicorn --app-dir "' + $AppSource + '" main:app --host 127.0.0.1 --port ' + $Port +
        ' --log-level warning --no-access-log')
}

function Get-ODSDesktopLogTail([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
    $lines = @(Get-Content -LiteralPath $Path -Tail 12 -Encoding UTF8 | Where-Object { $_ -and $_ -notmatch '(?i)api[_-]?key|authorization|bearer' })
    if (-not $lines.Count) { return '' }
    return ' Last lines of ' + (Split-Path -Leaf $Path) + ': ' + ($lines -join ' | ')
}

function Wait-ODSDesktopApiHealth([int]$Port, $Process, [string]$ErrorLog, [int]$Seconds = $script:ODSDesktopApiStartupSeconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if ($Process.HasExited) {
            throw ("The ODS app stopped during startup (exit code $($Process.ExitCode))." + (Get-ODSDesktopLogTail $ErrorLog))
        }
        if ((Invoke-ODSNativeLlamaHttp -Port $Port -Path '/health' -TimeoutMilliseconds 2000).StatusCode -eq 200) { return }
        Start-Sleep -Milliseconds 500
    }
    throw ("The ODS app did not answer within $Seconds seconds." + (Get-ODSDesktopLogTail $ErrorLog))
}

function Test-ODSDesktopModelNeedsRestart([bool]$Wanted, [string]$State, [uint32]$LastResult) {
    # Pure: restart only a wanted model task that a closed console window
    # ended; a stopped, disabled or running task is left alone.
    return $Wanted -and $State -cin @('Ready', 'Queued') -and $LastResult -eq $script:ODSDesktopConsoleClosedExit
}

function Repair-ODSDesktopModelTask($Paths) {
    <#
    .SYNOPSIS
        Restarts main's model task when Test-ODSDesktopModelNeedsRestart says
        so. Returns $true when it started the task.
    #>
    if (-not (Test-Path -LiteralPath $Paths.RuntimeOwner -PathType Leaf)) { return $false }
    $owner = Read-ODSNativeLlamaJson $Paths.RuntimeOwner
    $task = @(Get-ScheduledTask -TaskName ([string]$owner.Task) -TaskPath '\' -ErrorAction SilentlyContinue)
    if ($task.Count -ne 1) { return $false }
    $wanted = Test-ODSNativeLlamaWanted (Join-Path ([string]$owner.RuntimeDir) 'intent.json')
    $info = $task[0] | Get-ScheduledTaskInfo
    if (-not (Test-ODSDesktopModelNeedsRestart $wanted ([string]$task[0].State) ([uint32]$info.LastTaskResult))) { return $false }
    Start-ScheduledTask -TaskName ([string]$owner.Task) -TaskPath '\'
    return $true
}

function Test-ODSDesktopPortalNeedsRestart([bool]$Wanted, [string]$State, [uint32]$LastResult) {
    # Pure: restart a wanted Portal task that ended with an error. A clean exit
    # (0) means its intent said stopped; a task that never ran is left to its
    # sign-in trigger; a running, queued or disabled task is left alone.
    return $Wanted -and $State -ceq 'Ready' -and $LastResult -ne 0 -and $LastResult -ne $script:ODSDesktopTaskNeverRan
}

function Repair-ODSDesktopPortalTask($PortalPaths) {
    <#
    .SYNOPSIS
        Restarts the Portal task when Test-ODSDesktopPortalNeedsRestart says
        so. Returns $true when it started the task.
    #>
    $task = @(Get-ScheduledTask -TaskName (Get-ODSDesktopPortalTaskName) -TaskPath '\' -ErrorAction SilentlyContinue)
    if ($task.Count -ne 1) { return $false }
    $wanted = Test-ODSNativeLlamaWanted $PortalPaths.Intent
    $info = $task[0] | Get-ScheduledTaskInfo
    if (-not (Test-ODSDesktopPortalNeedsRestart $wanted ([string]$task[0].State) ([uint32]$info.LastTaskResult))) { return $false }
    Start-ScheduledTask -TaskName $task[0].TaskName -TaskPath '\'
    return $true
}

function Write-ODSDesktopLaunchLog($Paths, [string]$Message) {
    Add-Content -LiteralPath (Join-Path $Paths.Logs 'launch-api.log') -Encoding UTF8 -Value ('{0:o} {1}' -f (Get-Date), $Message)
}

function Wait-ODSDesktopApiExit($Process, $Paths) {
    # Waits for the app to exit, checking the model and Portal tasks every 30 seconds.
    $portalPaths = Get-ODSDesktopPortalPaths (Join-Path (Split-Path -Parent $Paths.Root) 'portal')
    $modelRestarts = 0
    $portalRestarts = 0
    while (-not $Process.WaitForExit($script:ODSDesktopModelWatchSeconds * 1000)) {
        try {
            if ($modelRestarts -lt $script:ODSDesktopModelRestartLimit -and (Repair-ODSDesktopModelTask $Paths)) {
                $modelRestarts++
                Write-ODSDesktopLaunchLog $Paths ('Restarted the model task after its console window was closed ({0} of {1}).' -f $modelRestarts, $script:ODSDesktopModelRestartLimit)
            }
            if ($portalRestarts -lt $script:ODSDesktopPortalRestartLimit -and (Repair-ODSDesktopPortalTask $portalPaths)) {
                $portalRestarts++
                Write-ODSDesktopLaunchLog $Paths ('Restarted Portal after it stopped with an error ({0} of {1}); the reason is in {2}.' -f $portalRestarts, $script:ODSDesktopPortalRestartLimit, $portalPaths.Ready)
            }
        } catch [Microsoft.Management.Infrastructure.CimException] {
            # Task Scheduler unavailable for a moment: the next check retries.
            Write-ODSDesktopLaunchLog $Paths ('Model or Portal task check failed (non-fatal): ' + $_.Exception.Message)
        }
    }
}

function Invoke-ODSDesktopApi($Paths) {
    <#
    .SYNOPSIS
        Durable launcher body. Returns the app's exit code; throws with a
        plain reason, which launch-api.ps1 writes to api-ready.json.
    #>
    if (-not (Test-ODSNativeLlamaWanted $Paths.Intent)) { return 0 }
    $environment = ConvertFrom-ODSDesktopEnvText ([IO.File]::ReadAllText($Paths.Environment, [Text.UTF8Encoding]::new($false, $true)))
    $port = [int]$environment['DASHBOARD_API_PORT']
    # Sizes only at each start; setup's -Repair compares every hash.
    $problem = Test-ODSDesktopPythonInstall -Directory $Paths.Python -Requirements (Join-Path $Paths.AppSource 'requirements.lock')
    if ($problem) { throw "$problem Run ODS setup again to repair it." }
    if (Test-Path -LiteralPath $Paths.Ready) { Remove-Item -LiteralPath $Paths.Ready -Force }
    $null = Stop-ODSDesktopApi $Paths.Ownership
    $listener = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    if ($listener.Count) {
        $owner = Get-Process -Id $listener[0].OwningProcess -ErrorAction SilentlyContinue
        throw "Port $port is used by $(if ($owner) { $owner.ProcessName } else { 'another program' }); nothing was stopped. Close it, or run ODS setup again with -Port and a free port."
    }
    $null = New-Item -ItemType Directory -Force -Path $Paths.Logs
    $out = Join-Path $Paths.Logs 'dashboard-api.out.log'
    $err = Join-Path $Paths.Logs 'dashboard-api.err.log'
    foreach ($log in @($out, $err)) { if (Test-Path -LiteralPath $log) { Move-Item -LiteralPath $log -Destination "$log.1" -Force } }
    foreach ($key in $environment.Keys) { [Environment]::SetEnvironmentVariable($key, [string]$environment[$key], 'Process') }
    $pythonw = Join-Path $Paths.Python 'pythonw.exe'
    $child = Start-Process -FilePath $pythonw -ArgumentList (New-ODSDesktopApiArguments $Paths.AppSource $port) `
        -WorkingDirectory $Paths.AppSource -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    try {
        $null = $child.Handle
        $identity = [pscustomobject]@{ ProcessId = $child.Id; CreationDate = $child.StartTime; ExecutablePath = $pythonw; ExitedAt = $null }
        Write-ODSPortalProcessOwnership $Paths.Ownership ([pscustomobject]@{ ExecutablePath = $pythonw; Port = $port }) @($identity)
        try {
            Wait-ODSDesktopApiHealth $port $child $err
        } catch {
            if (-not $child.HasExited) { $child.Kill(); $null = $child.WaitForExit(30000) }
            throw
        }
        $ready = [ordered]@{ ProcessId = $child.Id; Port = $port; StartedAt = $child.StartTime.ToUniversalTime().ToString('o') }
        Write-ODSPrivateEnvFile -Path $Paths.Ready -Content ($ready | ConvertTo-Json -Compress)
        Wait-ODSDesktopApiExit $child $Paths
        return $child.ExitCode
    } finally {
        $child.Dispose()
    }
}
