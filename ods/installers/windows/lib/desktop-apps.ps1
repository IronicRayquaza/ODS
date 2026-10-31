# ============================================================================
# ODS Windows Desktop -- native apps from manifest recipes
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Install, start and remove native apps (extensions with a
#          native.windows recipe in their manifest.yaml) on the windows-desktop
#          profile, with no Docker and no WSL. Each app gets
#          %LOCALAPPDATA%\ODS\apps\<id> (bin, data, logs, launch.json) and one
#          per-user sign-in task, ODSApp-<id>-<SID>, that runs
#          desktop_app_supervisor.py on the pinned Python. Two recipe kinds:
#          zip (a pinned release zip with its own executable) and llama-server
#          (a pinned GGUF run by Desktop's own llama-server, behind an
#          owner-only API key file).
# Expects: private-file.ps1 (Write-ODSPrivateEnvFile), native-llama-runtime.ps1
#          (Read-ODSNativeLlamaJson), desktop-portal.ps1 (Get-ODSDesktopUserSid),
#          backend-contract.ps1 (New-ODSInteractiveScheduledTaskPrincipal),
#          desktop-python.ps1 (Save-ODSDesktopPinnedFile,
#          Invoke-ODSDesktopPinnedDownload), desktop-runtime.ps1
#          (Publish-ODSDesktopFolder)
# Provides: Get-ODSDesktopExtensionPaths, Get-ODSDesktopExtensionTaskName,
#           New-ODSDesktopExtensionPin, New-ODSDesktopExtensionLaunch,
#           Get-ODSDesktopInstalledExtensions, Install-ODSDesktopExtension,
#           Remove-ODSDesktopExtensions
# Modder notes: the recipe rules and placeholder resolution live in
#   dashboard-api/desktop_app_recipes.py only; this file asks the pinned
#   Python for a resolved recipe. App data survives uninstall unless removed.
# ============================================================================

$script:ODSDesktopExtensionTaskPrefix = 'ODSApp-'
$script:ODSDesktopExtensionReadySeconds = 150

function Get-ODSDesktopExtensionRoot { return (Join-Path $env:LOCALAPPDATA 'ODS\apps') }

function Get-ODSDesktopExtensionPaths([string]$Id, [string]$Root = (Get-ODSDesktopExtensionRoot)) {
    if ($Id -cnotmatch '^[a-z0-9][a-z0-9-]{0,40}$') { throw "'$Id' is not an ODS app id." }
    $folder = Join-Path $Root $Id
    return [pscustomobject]@{
        Root = $folder
        Bin = Join-Path $folder 'bin'
        Data = Join-Path $folder 'data'
        Logs = Join-Path $folder 'logs'
        Launch = Join-Path $folder 'launch.json'
        Ready = Join-Path $folder 'ready.json'
        Marker = Join-Path $folder 'bin\ods-app.json'
    }
}

function Get-ODSDesktopExtensionTaskName([string]$Id, [string]$UserSid = (Get-ODSDesktopUserSid)) {
    return ($script:ODSDesktopExtensionTaskPrefix + $Id + '-' + $UserSid)
}

function New-ODSDesktopExtensionPin($Recipe) {
    # The shape Save-ODSDesktopPinnedFile checks: file, url, size, sha256.
    return [pscustomobject]@{ file = [string]$Recipe.file; url = [string]$Recipe.url
        size = [long]$Recipe.size; sha256 = [string]$Recipe.sha256 }
}

function New-ODSDesktopExtensionLaunch($Recipe, $Paths, [string]$LlamaServer) {
    <#
    .SYNOPSIS
        launch.json for desktop_app_supervisor.py. Pure. A llama-server
        recipe runs Desktop's own pinned llama-server.exe.
    #>
    if ($Recipe.kind -ceq 'llama-server') {
        if (-not $LlamaServer) { throw "$($Recipe.name) runs on ODS Desktop's llama-server, and setup did not find it." }
        $executable = $LlamaServer
    } else {
        $executable = Join-Path $Paths.Bin ([string]$Recipe.executable)
    }
    $appEnvironment = [ordered]@{}
    foreach ($property in $Recipe.env.PSObject.Properties) { $appEnvironment[$property.Name] = [string]$property.Value }
    return [ordered]@{
        name = [string]$Recipe.name; executablePath = $executable
        args = @($Recipe.args | ForEach-Object { [string]$_ }); env = $appEnvironment; port = [int]$Recipe.port
        health = [string]$Recipe.health; data = $Paths.Data
    }
}

function Get-ODSDesktopExtensionRecipe([string]$AppSource, [string]$ServicesDir, [string]$PythonDir, [string]$Id, $Paths) {
    $output = & (Join-Path $PythonDir 'python.exe') -I (Join-Path $AppSource 'desktop_app_recipes.py') resolve $ServicesDir $Id $Paths.Data $Paths.Bin
    if ($LASTEXITCODE -ne 0) { throw "'$Id' has no usable native Windows recipe." }
    return (($output -join "`n") | ConvertFrom-Json)
}

function Install-ODSDesktopExtensionFiles($Recipe, $Paths) {
    # A rerun with the same pinned file keeps what is installed.
    if ((Test-Path -LiteralPath $Paths.Marker -PathType Leaf) -and [string](Read-ODSNativeLlamaJson $Paths.Marker).Sha256 -ceq [string]$Recipe.sha256) { return }
    $download = Join-Path $env:TEMP ('ods-app-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $download
    try {
        $pinned = Save-ODSDesktopPinnedFile (New-ODSDesktopExtensionPin $Recipe) $download ${function:Invoke-ODSDesktopPinnedDownload}
        Publish-ODSDesktopFolder $Paths.Bin {
            param($stage)
            if ($Recipe.kind -ceq 'llama-server') {
                # The model is the whole payload; the pin already checked it.
                Move-Item -LiteralPath $pinned -Destination (Join-Path $stage ([string]$Recipe.file))
            } else {
                Expand-Archive -LiteralPath $pinned -DestinationPath $stage
                if (-not (Test-Path -LiteralPath (Join-Path $stage ([string]$Recipe.executable)) -PathType Leaf)) {
                    throw "The $($Recipe.name) download has no $($Recipe.executable)."
                }
            }
            Write-ODSPrivateEnvFile -Path (Join-Path $stage 'ods-app.json') -Content (@{ Version = [string]$Recipe.version; Sha256 = [string]$Recipe.sha256 } | ConvertTo-Json -Compress)
        }
    } finally {
        Remove-Item -LiteralPath $download -Recurse -Force
    }
}

function New-ODSDesktopExtensionKey($Paths) {
    # llama-server apps never run without a key. Owner-only, and kept across
    # reinstalls so a client that already has it keeps working.
    $keyFile = Join-Path $Paths.Data 'api-key'
    if (Test-Path -LiteralPath $keyFile -PathType Leaf) { return }
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    Write-ODSPrivateEnvFile -Path $keyFile -Content ('ods-' + (($bytes | ForEach-Object { $_.ToString('x2') }) -join ''))
}

function Get-ODSDesktopInstalledExtensions([string]$Root = (Get-ODSDesktopExtensionRoot)) {
    # The apps a setup rerun repairs, so a llama.cpp upgrade never leaves one
    # pointing at a removed llama-server.exe.
    if (-not (Test-Path -LiteralPath $Root)) { return @() }
    return @(Get-ChildItem -LiteralPath $Root -Directory | Where-Object {
        $_.Name -cmatch '^[a-z0-9][a-z0-9-]{0,40}$' -and (Test-Path -LiteralPath (Join-Path $_.FullName 'bin\ods-app.json') -PathType Leaf)
    } | ForEach-Object { $_.Name })
}

function Register-ODSDesktopExtensionTask([string]$Id, $Paths, [string]$PythonDir, [string]$Supervisor) {
    $action = New-ScheduledTaskAction -Execute (Join-Path $PythonDir 'pythonw.exe') -Argument ('"' + $Supervisor + '" "' + $Paths.Root + '"') `
        -WorkingDirectory $Paths.Root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User (Get-ODSDesktopUserSid)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName (Get-ODSDesktopExtensionTaskName $Id) -TaskPath '\' -Action $action -Trigger $trigger -Settings $settings `
        -Principal (New-ODSInteractiveScheduledTaskPrincipal -RunLevel Limited) `
        -Description "ODS Desktop app: $Id on 127.0.0.1. Starts at sign-in." -Force | Out-Null
}

function Stop-ODSDesktopExtension([string]$Id) {
    $task = Get-ScheduledTask -TaskName (Get-ODSDesktopExtensionTaskName $Id) -TaskPath '\' -ErrorAction SilentlyContinue
    # Ending the task ends the supervisor; its kill-on-close job ends the app.
    if ($task -and [string]$task.State -eq 'Running') { Stop-ScheduledTask -TaskName $task.TaskName -TaskPath '\' }
}

function Wait-ODSDesktopExtensionReady($Paths, [int]$Port) {
    $deadline = (Get-Date).AddSeconds($script:ODSDesktopExtensionReadySeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $Paths.Ready -PathType Leaf) {
            $ready = Read-ODSNativeLlamaJson $Paths.Ready
            if ($ready.Error) { throw ([string]$ready.Error) }
            $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
            if (-not $listeners.Count -or @($listeners | Where-Object { $_.LocalAddress -ne '127.0.0.1' }).Count) {
                throw "The app on port $Port is not listening on 127.0.0.1 only."
            }
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "The app did not start within $($script:ODSDesktopExtensionReadySeconds) seconds; see $($Paths.Logs)."
}

function Install-ODSDesktopExtension {
    <#
    .SYNOPSIS
        Install or repair one native app and start it. Returns its local address.
    #>
    param([string]$Id, [string]$AppSource, [string]$ServicesDir, [string]$PythonDir, [string]$LlamaServer)
    $paths = Get-ODSDesktopExtensionPaths $Id
    $recipe = Get-ODSDesktopExtensionRecipe $AppSource $ServicesDir $PythonDir $Id $paths
    Stop-ODSDesktopExtension $Id
    $listener = @(Get-NetTCPConnection -LocalPort ([int]$recipe.port) -State Listen -ErrorAction SilentlyContinue)
    if ($listener.Count) { throw "$($recipe.name) needs port $($recipe.port), which another program is using; nothing was stopped." }
    foreach ($folder in @($paths.Root, $paths.Data, $paths.Logs)) { $null = New-Item -ItemType Directory -Force -Path $folder }
    Install-ODSDesktopExtensionFiles $recipe $paths
    if ($recipe.kind -ceq 'llama-server') { New-ODSDesktopExtensionKey $paths }
    Write-ODSPrivateEnvFile -Path $paths.Launch -Content ((New-ODSDesktopExtensionLaunch $recipe $paths $LlamaServer) | ConvertTo-Json -Depth 6)
    if (Test-Path -LiteralPath $paths.Ready) { Remove-Item -LiteralPath $paths.Ready -Force }
    Register-ODSDesktopExtensionTask $Id $paths $PythonDir (Join-Path $AppSource 'desktop_app_supervisor.py')
    Start-ScheduledTask -TaskName (Get-ODSDesktopExtensionTaskName $Id) -TaskPath '\'
    Wait-ODSDesktopExtensionReady $paths ([int]$recipe.port)
    return "http://127.0.0.1:$($recipe.port)/"
}

function Remove-ODSDesktopExtensions([bool]$RemoveData, [string]$Root = (Get-ODSDesktopExtensionRoot)) {
    <#
    .SYNOPSIS
        Uninstall: every app's task and files; data stays unless RemoveData.
        Returns the kept data folders.
    #>
    $kept = @()
    if (-not (Test-Path -LiteralPath $Root)) { return $kept }
    foreach ($folder in @(Get-ChildItem -LiteralPath $Root -Directory)) {
        $taskName = Get-ODSDesktopExtensionTaskName $folder.Name
        if (Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue) {
            Stop-ODSDesktopExtension $folder.Name
            Unregister-ScheduledTask -TaskName $taskName -TaskPath '\' -Confirm:$false
        }
        foreach ($item in @(Get-ChildItem -LiteralPath $folder.FullName -Force)) {
            if (-not $RemoveData -and $item.Name -ceq 'data') { $kept += $item.FullName } else { Remove-Item -LiteralPath $item.FullName -Recurse -Force }
        }
        if (-not @(Get-ChildItem -LiteralPath $folder.FullName -Force).Count) { Remove-Item -LiteralPath $folder.FullName -Force }
    }
    if (-not @(Get-ChildItem -LiteralPath $Root -Force).Count) { Remove-Item -LiteralPath $Root -Force }
    return $kept
}
