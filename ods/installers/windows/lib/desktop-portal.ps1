# ============================================================================
# ODS Windows Desktop -- native Portal (OpenClaw agent, ingress, edge)
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Install and start Portal for the windows-desktop profile with no
#          Docker and no WSL: a private Node, the pinned OpenClaw package, the
#          ODS plugin, the native ingress and the upstream Pixel edge, all
#          supervised by one per-user sign-in task (ODSPortal-<SID>) inside a
#          kill-on-close Job Object. Everything listens on 127.0.0.1 or a
#          per-user named pipe.
# Expects: private-file.ps1 (Write-ODSPrivateEnvFile), native-llama-runtime.ps1
#          (New-ODSNativeLlamaApiKey, Read-ODSNativeLlamaJson,
#          Get-ODSNativeLlamaFileSha256), wsl-portal-amd.ps1
#          (Get-ODSPortalUserSid, Get-ODSPortalRuntimeDir),
#          backend-contract.ps1 (New-ODSInteractiveScheduledTaskPrincipal),
#          desktop-python.ps1 (Assert-ODSDesktopDownloadPin,
#          Save-ODSDesktopPinnedFile, Invoke-ODSDesktopPinnedDownload,
#          Invoke-ODSDesktopStep), desktop-runtime.ps1
#          (Publish-ODSDesktopFolder, ConvertTo-ODSDesktopEnvText)
# Provides: Get-ODSDesktopPortalPaths, Get-ODSDesktopPortalPins,
#           Get-ODSDesktopPortalBudget, New-ODSDesktopPortalConfig,
#           Install-ODSDesktopPortal, Stop-ODSDesktopPortal,
#           Get-ODSDesktopPortalTaskName
# Modder notes: the pure functions (pins, budget, config) are what the
#   contract test checks; the budget numbers are the ones
#   installers/lib/pixel-runtime-budget.py applies on Linux, which cannot run
#   here (POSIX owner and mode checks). Portal refuses a context below
#   context_policy.PIXEL_MIN_CONTEXT rather than raising it silently.
# ============================================================================

$script:ODSDesktopPortalTaskPrefix = 'ODSPortal-'
$script:ODSDesktopPortalMinContext = 16384
$script:ODSDesktopPortalGatewayPort = 18789
$script:ODSDesktopPortalEdgePort = 9595
$script:ODSDesktopPortalReadySeconds = 150

function Get-ODSDesktopPortalPaths([string]$Root = (Join-Path $env:LOCALAPPDATA 'ODS\portal')) {
    $runtime = Join-Path $Root 'runtime'
    return [pscustomobject]@{
        Root = $Root
        Node = Join-Path $Root 'node'
        OpenClaw = Join-Path $Root 'openclaw'
        NpmCache = Join-Path $Root 'npm-cache'
        Runtime = $runtime
        Supervisor = Join-Path $runtime 'pixel-agent\windows\portal_supervisor.py'
        Plugin = Join-Path $runtime 'pixel-agent\plugin'
        SandboxPlugin = Join-Path $runtime 'pixel-agent\windows\plugin'
        SandboxRunner = Join-Path $runtime 'pixel-agent\windows\sandbox_run.py'
        SandboxHelpers = Join-Path $runtime 'pixel-agent\windows'
        ExecControl = Join-Path $Root 'home\.openclaw\.ods-exec-control'
        State = Join-Path $Root 'state'
        Config = Join-Path $Root 'state\openclaw.json'
        Home = Join-Path $Root 'home'
        Workspace = Join-Path $Root 'workspace'
        ChatState = Join-Path $Root 'chat-state'
        Logs = Join-Path $Root 'logs'
        Environment = Join-Path $Root 'portal.env'
        Intent = Join-Path $Root 'portal-intent.json'
        Ready = Join-Path $Root 'portal-ready.json'
    }
}

function Get-ODSDesktopPortalTaskName { return ($script:ODSDesktopPortalTaskPrefix + (Get-ODSPortalUserSid)) }

function Get-ODSDesktopPortalPipe { return ('\\.\pipe\ods-pixel-ingress-' + (Get-ODSPortalUserSid)) }

function Get-ODSDesktopPortalSandboxProfile([string]$UserSid = (Get-ODSPortalUserSid)) {
    # sandbox_run.py profile_name: one AppContainer per Windows account.
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $digest = $sha.ComputeHash([Text.Encoding]::ASCII.GetBytes($UserSid)) } finally { $sha.Dispose() }
    return 'ODS.Portal.Sandbox.' + (-join ($digest | ForEach-Object { $_.ToString('x2') })).Substring(0, 24)
}

function Get-ODSDesktopPortalPins([string]$SourceRoot) {
    # Node comes from the Desktop receipt; OpenClaw from the Pixel release
    # manifest, the same pin the Linux installer uses.
    $desktop = [IO.File]::ReadAllText((Join-Path $SourceRoot 'config\windows-desktop.json')) | ConvertFrom-Json
    $node = $desktop.portalNode
    Assert-ODSDesktopDownloadPin $node 'Portal Node' '^https://nodejs\.org/dist/v[0-9.]+/'
    $manifest = [IO.File]::ReadAllText((Join-Path $SourceRoot 'vendor\pixel\RELEASE-MANIFEST.json')) | ConvertFrom-Json
    $package = $manifest.openclawPackage
    $version = [string]$manifest.openclaw
    if ($version -cnotmatch '^[0-9]{4}\.[0-9]+\.[0-9]+$') { throw "RELEASE-MANIFEST.json has no valid OpenClaw version." }
    if ([string]$package.url -cne "https://registry.npmjs.org/openclaw/-/openclaw-$version.tgz" -or
        [string]$package.sha256 -cnotmatch '^[a-f0-9]{64}$' -or [string]$package.integrity -cnotmatch '^sha512-[A-Za-z0-9+/]{86}==$') {
        throw "RELEASE-MANIFEST.json has no valid OpenClaw package pin."
    }
    return [pscustomobject]@{
        Node = $node
        OpenClaw = [pscustomobject]@{ Version = $version; File = "openclaw-$version.tgz"; Url = [string]$package.url
            Sha256 = [string]$package.sha256; Integrity = [string]$package.integrity }
    }
}

function Get-ODSDesktopPortalBudget([int]$Context, [int]$MaxTokens, [string]$ModelId) {
    <#
    .SYNOPSIS
        The context-derived limits pixel-runtime-budget.py sets for a route.
    #>
    if ($Context -lt $script:ODSDesktopPortalMinContext) {
        throw "Portal needs at least $($script:ODSDesktopPortalMinContext) tokens of context; this model runs with $Context."
    }
    if ($MaxTokens -lt 1 -or $MaxTokens -gt $Context) { throw "The model output limit must be between 1 and the context size." }
    $label = $ModelId.ToLowerInvariant()
    $small = @([regex]::Matches($label, '(?<![a-z0-9.])(\d+(?:\.\d+)?)\s*b(?![a-z0-9])') |
        Where-Object { [double]$_.Groups[1].Value -le 4 }).Count -gt 0
    $lean = ($Context -lt 32768) -or $small
    return [pscustomobject]@{
        Lean = $lean
        ReserveTokens = [int][math]::Floor(($Context + 4 * $MaxTokens + 4) / 5)
        KeepRecentTokens = [int][math]::Max(512, [math]::Min(20000, [math]::Floor($Context / 16)))
        ToolResultMaxChars = [int][math]::Max(4000, [math]::Min(16000, [math]::Floor($Context / 4)))
        BootstrapMaxChars = $(if ($lean) { 2000 } else { 14000 })
        BootstrapTotalMaxChars = $(if ($lean) { 6000 } else { 36000 })
        ContextInjection = $(if ($lean) { 'never' } else { 'continuation-skip' })
        # Desktop starts llama-server with reasoning off; Qwen templates need it
        # said explicitly or they spend the output budget thinking.
        QwenThinkingOff = $label.Contains('qwen')
    }
}

function New-ODSDesktopPortalConfig {
    <#
    .SYNOPSIS
        The OpenClaw configuration for the Desktop Portal, as an ordered
        dictionary. Pure: same inputs, same result.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Token,
        [Parameter(Mandatory = $true)][int]$LlamaPort,
        [Parameter(Mandatory = $true)][string]$LlamaKey,
        [Parameter(Mandatory = $true)][string]$ModelId,
        [Parameter(Mandatory = $true)][int]$Context,
        [Parameter(Mandatory = $true)][string]$Workspace,
        [Parameter(Mandatory = $true)][string]$PixelPlugin,
        $Sandbox = $null,
        [int]$GatewayPort = $script:ODSDesktopPortalGatewayPort
    )
    if ($ModelId -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._+-]{0,200}$') { throw "The model name cannot be used for Portal: $ModelId" }
    $maxTokens = [int][math]::Min(4096, [math]::Floor($Context / 4))
    $budget = Get-ODSDesktopPortalBudget -Context $Context -MaxTokens $maxTokens -ModelId $ModelId
    $model = "ods-local/$ModelId"
    # Sandbox mode (an AppContainer per command) when the Windows sandbox
    # plugin is present; otherwise no command runs at all. The ODS plugin
    # accepts only these two consistent shapes.
    $sandboxMode = if ($Sandbox) { [ordered]@{ mode = 'all'; scope = 'agent'; workspaceAccess = 'rw'; backend = 'ods-windows' } }
        else { [ordered]@{ mode = 'off' } }
    $exec = if ($Sandbox) { [ordered]@{ host = 'sandbox' } } else { [ordered]@{ host = 'gateway'; security = 'deny' } }
    $agent = [ordered]@{
        id = 'pixel'; name = 'Pixel'; default = $true; workspace = $Workspace; model = $model
        heartbeat = [ordered]@{ every = '0m' }
        experimental = [ordered]@{ localModelLean = $false }
        bootstrapMaxChars = $budget.BootstrapMaxChars; bootstrapTotalMaxChars = $budget.BootstrapTotalMaxChars
        contextInjection = $budget.ContextInjection
        contextLimits = [ordered]@{ toolResultMaxChars = $budget.ToolResultMaxChars }
    }
    if ($budget.QwenThinkingOff) { $agent.params = [ordered]@{ chat_template_kwargs = [ordered]@{ enable_thinking = $false } } }
    $plugins = [ordered]@{
        allow = @('pixel-ods')
        load = [ordered]@{ paths = @($PixelPlugin) }
        entries = [ordered]@{
            'pixel-ods' = [ordered]@{ enabled = $true; hooks = [ordered]@{ allowConversationAccess = $true }
                config = [ordered]@{ modelContextWindow = $Context; modelImageInput = 'unsupported'; leanPrompt = $budget.Lean } }
        }
    }
    if ($Sandbox) {
        $plugins.allow += 'ods-windows-sandbox'
        $plugins.load.paths += [string]$Sandbox.Plugin
        $plugins.entries['ods-windows-sandbox'] = [ordered]@{ enabled = $true
            config = [ordered]@{ python = [string]$Sandbox.Python; runner = [string]$Sandbox.Runner; profile = [string]$Sandbox.Profile
                toolPath = [string]$Sandbox.ToolPath; controlRoot = [string]$Sandbox.ControlRoot } }
    }
    return [ordered]@{
        gateway = [ordered]@{
            mode = 'local'; bind = 'loopback'; port = $GatewayPort
            auth = [ordered]@{ mode = 'token'; token = $Token }
            http = [ordered]@{ endpoints = [ordered]@{ chatCompletions = [ordered]@{ enabled = $true } } }
        }
        models = [ordered]@{
            mode = 'merge'
            providers = [ordered]@{
                'ods-local' = [ordered]@{
                    baseUrl = "http://127.0.0.1:$LlamaPort/v1"; apiKey = $LlamaKey; api = 'openai-completions'
                    # Also the idle watchdog: a CPU route reads a long first prompt slowly.
                    timeoutSeconds = 1800
                    models = @([ordered]@{ id = $ModelId; name = "ODS Local $ModelId"; reasoning = $false; input = @('text')
                        cost = [ordered]@{ input = 0; output = 0; cacheRead = 0; cacheWrite = 0 }
                        contextWindow = $Context; maxTokens = $maxTokens })
                }
            }
        }
        agents = [ordered]@{
            defaults = [ordered]@{
                model = [ordered]@{ primary = $model }; workspace = $Workspace; sandbox = $sandboxMode
                timeoutSeconds = 1800; bootstrapMaxChars = 32000; bootstrapTotalMaxChars = 96000
                contextInjection = 'continuation-skip'
                compaction = [ordered]@{ reserveTokens = $budget.ReserveTokens; reserveTokensFloor = 0; timeoutSeconds = 1800
                    keepRecentTokens = $budget.KeepRecentTokens }
            }
            list = @($agent)
        }
        tools = [ordered]@{ exec = $exec
            toolSearch = [ordered]@{ enabled = $true; mode = 'tools'; searchDefaultLimit = 5; maxSearchLimit = 10 } }
        plugins = $plugins
    }
}

function Save-ODSDesktopPortalPackage($Pin, [string]$Directory) {
    # npm packages carry SHA-256 and SHA-512 (integrity) pins but no size.
    $target = Join-Path $Directory $Pin.File
    $code = Invoke-ODSDesktopStep ${function:Invoke-ODSDesktopPinnedDownload} @($Pin.Url, $target)
    if ($code -ne 0) { throw "Could not download $($Pin.File) (curl exit $code). Check the connection and run setup again." }
    $sha512 = [Security.Cryptography.SHA512]::Create()
    try {
        $stream = [IO.File]::OpenRead($target)
        try { $integrity = 'sha512-' + [Convert]::ToBase64String($sha512.ComputeHash($stream)) } finally { $stream.Dispose() }
    } finally { $sha512.Dispose() }
    if ((Get-ODSNativeLlamaFileSha256 $target) -cne $Pin.Sha256 -or $integrity -cne $Pin.Integrity) {
        Remove-Item -LiteralPath $target -Force
        throw "$($Pin.File) does not match its pin. It was not used."
    }
    return $target
}

function Get-ODSDesktopPortalOpenClawVersion($Paths) {
    $package = Join-Path $Paths.OpenClaw 'node_modules\openclaw\package.json'
    if (-not (Test-Path -LiteralPath $package -PathType Leaf)) { return '' }
    return [string]([IO.File]::ReadAllText($package) | ConvertFrom-Json).version
}

function Install-ODSDesktopPortalNode($Pin, $Paths) {
    $node = Join-Path $Paths.Node 'node.exe'
    $marker = Join-Path $Paths.Node 'ods-node.json'
    if ((Test-Path -LiteralPath $node -PathType Leaf) -and (Test-Path -LiteralPath $marker -PathType Leaf) -and
        [string](Read-ODSNativeLlamaJson $marker).Sha256 -ceq [string]$Pin.sha256) { return }
    $download = Join-Path $env:TEMP ('ods-portal-node-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $download
    try {
        $zip = Save-ODSDesktopPinnedFile $Pin $download ${function:Invoke-ODSDesktopPinnedDownload}
        Publish-ODSDesktopFolder $Paths.Node {
            param($stage)
            Expand-Archive -LiteralPath $zip -DestinationPath $stage
            $inner = Join-Path $stage ([IO.Path]::GetFileNameWithoutExtension([string]$Pin.file))
            foreach ($item in @(Get-ChildItem -LiteralPath $inner -Force)) { Move-Item -LiteralPath $item.FullName -Destination $stage }
            Remove-Item -LiteralPath $inner -Force
            Write-ODSPrivateEnvFile -Path (Join-Path $stage 'ods-node.json') -Content (@{ Version = [string]$Pin.version; Sha256 = [string]$Pin.sha256 } | ConvertTo-Json -Compress)
        }
    } finally {
        Remove-Item -LiteralPath $download -Recurse -Force
    }
}

function Invoke-ODSDesktopPortalNpm($Paths, [string]$Package) {
    # The private Node's own npm, with a private cache and no install scripts:
    # OpenClaw's npm-shrinkwrap.json pins every dependency with its integrity.
    $node = Join-Path $Paths.Node 'node.exe'
    $npm = Join-Path $Paths.Node 'node_modules\npm\bin\npm-cli.js'
    $variables = @{ npm_config_cache = $Paths.NpmCache; npm_config_update_notifier = 'false'; npm_config_fund = 'false'
        npm_config_audit = 'false'; npm_config_ignore_scripts = 'true' }
    $saved = @{}
    foreach ($name in $variables.Keys) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process'); [Environment]::SetEnvironmentVariable($name, $variables[$name], 'Process') }
    try {
        & $node $npm install --global --prefix $Paths.OpenClaw --ignore-scripts --no-audit --no-fund $Package | Out-Host
        return $LASTEXITCODE
    } finally {
        foreach ($name in $variables.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
    }
}

function Install-ODSDesktopOpenClaw($Pin, $Paths) {
    if ((Get-ODSDesktopPortalOpenClawVersion $Paths) -ceq $Pin.Version) { return }
    $download = Join-Path $env:TEMP ('ods-portal-openclaw-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $download
    try {
        $package = Save-ODSDesktopPortalPackage $Pin $download
        if (Test-Path -LiteralPath $Paths.OpenClaw) { Remove-Item -LiteralPath $Paths.OpenClaw -Recurse -Force }
        $code = Invoke-ODSDesktopStep ${function:Invoke-ODSDesktopPortalNpm} @($Paths, $package)
        if ($code -ne 0) { throw "Installing OpenClaw $($Pin.Version) failed (npm exit $code). Run setup again." }
    } finally {
        Remove-Item -LiteralPath $download -Recurse -Force
    }
    $installed = Get-ODSDesktopPortalOpenClawVersion $Paths
    if ($installed -cne $Pin.Version) { throw "OpenClaw $($Pin.Version) was expected after install; found '$installed'." }
}

function Publish-ODSDesktopPortalRuntime([string]$SourceRoot, $Paths) {
    # The ODS plugin, the ingress modules, the Windows supervisor and the
    # upstream Pixel edge, copied so the task never runs from a checkout.
    $agent = Join-Path $SourceRoot 'extensions\services\pixel-agent'
    $edge = Join-Path $SourceRoot 'extensions\services\pixel-edge'
    Publish-ODSDesktopFolder $Paths.Runtime {
        param($stage)
        foreach ($folder in @('plugin', 'host', 'windows', 'windows\plugin')) {
            $target = Join-Path $stage "pixel-agent\$folder"
            $null = New-Item -ItemType Directory -Force -Path $target
            foreach ($item in @(Get-ChildItem -LiteralPath (Join-Path $agent $folder) -File | Where-Object { $_.Extension -cin @('.js', '.mjs', '.json', '.py') })) {
                Copy-Item -LiteralPath $item.FullName -Destination $target
            }
        }
        $null = New-Item -ItemType Directory -Force -Path (Join-Path $stage 'pixel-edge')
        foreach ($item in @(Get-ChildItem -LiteralPath $edge -File -Filter '*.py')) {
            Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $stage 'pixel-edge')
        }
    }
}

function Initialize-ODSDesktopPortalSandbox($Paths, [string]$PythonDir) {
    <#
    .SYNOPSIS
        Create the account's AppContainer profile and its ACL grants once:
        modify on the workspace, read and execute on the toolchain (the private
        Node and the pinned Python) and the helper scripts. Returns the
        sandbox plugin settings.
    #>
    $null = New-Item -ItemType Directory -Force -Path $Paths.ExecControl
    $sandboxProfile = Get-ODSDesktopPortalSandboxProfile
    $python = Join-Path $PythonDir 'python.exe'
    & $python -I $Paths.SandboxRunner setup --profile $sandboxProfile --workspace $Paths.Workspace `
        --read $Paths.Node --read $PythonDir --read $Paths.SandboxHelpers | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "The Portal sandbox could not be prepared (exit $LASTEXITCODE)." }
    return [pscustomobject]@{ Plugin = $Paths.SandboxPlugin; Python = $python; Runner = $Paths.SandboxRunner; Profile = $sandboxProfile
        ToolPath = (@($Paths.Node, $PythonDir, (Join-Path $env:SystemRoot 'System32')) -join ';'); ControlRoot = $Paths.ExecControl }
}

function Read-ODSDesktopPortalSecrets($Paths) {
    # Keys survive reruns; a missing or partial file gets fresh ones.
    $previous = @{}
    if (Test-Path -LiteralPath $Paths.Environment -PathType Leaf) {
        $previous = ConvertFrom-ODSDesktopEnvText ([IO.File]::ReadAllText($Paths.Environment, [Text.UTF8Encoding]::new($false, $true)))
    }
    $secrets = [ordered]@{}
    foreach ($name in @('GATEWAY_TOKEN', 'PIXEL_OPENWEBUI_KEY', 'PIXEL_PREVIEW_PROXY_KEY')) {
        $secrets[$name] = if ([string]$previous[$name] -cmatch '^[A-Za-z0-9_-]{32,128}$') { [string]$previous[$name] } else { New-ODSNativeLlamaApiKey }
    }
    return $secrets
}

function Assert-ODSDesktopPortalPortFree([int]$Port) {
    $listener = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($listener.Count) {
        $owner = Get-Process -Id $listener[0].OwningProcess -ErrorAction SilentlyContinue
        throw "Portal needs port $Port, which $(if ($owner) { $owner.ProcessName } else { 'another program' }) is using; nothing was stopped. Close it and run setup again."
    }
}

function Stop-ODSDesktopPortal($Paths) {
    # Intent first, so a queued sign-in start exits at once. Stopping the task
    # ends the supervisor; its kill-on-close job ends gateway, ingress and edge.
    if (Test-Path -LiteralPath $Paths.Root) { Write-ODSPrivateEnvFile -Path $Paths.Intent -Content '{"State":"stopped"}' }
    $task = Get-ScheduledTask -TaskName (Get-ODSDesktopPortalTaskName) -TaskPath '\' -ErrorAction SilentlyContinue
    if ($task -and [string]$task.State -eq 'Running') { Stop-ScheduledTask -TaskName $task.TaskName -TaskPath '\' }
    if (Test-Path -LiteralPath $Paths.Ready -PathType Leaf) {
        $ready = Read-ODSNativeLlamaJson $Paths.Ready
        foreach ($processId in @($ready.ProcessId) + @($ready.Children)) {
            if (-not $processId) { continue }
            $process = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$processId)" -ErrorAction SilentlyContinue
            # Only a process that still runs from this Portal's folders is ours.
            if ($process -and ([string]$process.CommandLine).Contains($Paths.Root)) { Stop-Process -Id ([int]$processId) -Force }
        }
        Remove-Item -LiteralPath $Paths.Ready -Force
    }
}

function Register-ODSDesktopPortalTask($Paths, [string]$PythonDir) {
    $pythonw = Join-Path $PythonDir 'pythonw.exe'
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"' + $Paths.Supervisor + '" "' + $Paths.Root + '"') -WorkingDirectory $Paths.Root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User (Get-ODSPortalUserSid)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName (Get-ODSDesktopPortalTaskName) -TaskPath '\' -Action $action -Trigger $trigger -Settings $settings `
        -Principal (New-ODSInteractiveScheduledTaskPrincipal -RunLevel Limited) `
        -Description 'ODS Desktop: Portal (the OpenClaw agent on 127.0.0.1). Starts at sign-in.' -Force | Out-Null
}

function Wait-ODSDesktopPortalReady($Paths) {
    $deadline = (Get-Date).AddSeconds($script:ODSDesktopPortalReadySeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $Paths.Ready -PathType Leaf) {
            $ready = Read-ODSNativeLlamaJson $Paths.Ready
            if ($ready.Error) { throw ([string]$ready.Error) }
            foreach ($port in @($script:ODSDesktopPortalGatewayPort, $script:ODSDesktopPortalEdgePort)) {
                $listener = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
                if ($listener.Count -ne 1 -or $listener[0].LocalAddress -ne '127.0.0.1') { throw "Portal port $port is not listening on 127.0.0.1 only." }
            }
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Portal did not start within $($script:ODSDesktopPortalReadySeconds) seconds; see $($Paths.Logs)."
}

function Install-ODSDesktopPortal {
    <#
    .SYNOPSIS
        Install or repair Portal against the running Desktop model. Returns the
        edge origin and key dashboard-api needs.
    #>
    param([string]$SourceRoot, $Paths, [string]$PythonDir, [string]$RuntimeDir)
    $runtime = Read-ODSNativeLlamaJson (Join-Path $RuntimeDir 'runtime.json')
    $llamaKey = ([IO.File]::ReadAllText((Join-Path $RuntimeDir 'api-key'))).Trim()
    $null = Get-ODSDesktopPortalBudget -Context ([int]$runtime.ContextSize) -MaxTokens 1 -ModelId ([string]$runtime.GgufFile)
    $pins = Get-ODSDesktopPortalPins $SourceRoot
    Stop-ODSDesktopPortal $Paths
    foreach ($port in @($script:ODSDesktopPortalGatewayPort, $script:ODSDesktopPortalEdgePort)) { Assert-ODSDesktopPortalPortFree $port }
    foreach ($folder in @($Paths.Root, $Paths.State, $Paths.Home, $Paths.Workspace, $Paths.ChatState, $Paths.Logs)) {
        $null = New-Item -ItemType Directory -Force -Path $folder
    }
    Install-ODSDesktopPortalNode $pins.Node $Paths
    Install-ODSDesktopOpenClaw $pins.OpenClaw $Paths
    Publish-ODSDesktopPortalRuntime $SourceRoot $Paths
    $sandbox = Initialize-ODSDesktopPortalSandbox $Paths $PythonDir
    $secrets = Read-ODSDesktopPortalSecrets $Paths
    $config = New-ODSDesktopPortalConfig -Token $secrets.GATEWAY_TOKEN -LlamaPort ([int]$runtime.Port) -LlamaKey $llamaKey `
        -ModelId ([string]$runtime.GgufFile) -Context ([int]$runtime.ContextSize) -Workspace $Paths.Workspace -PixelPlugin $Paths.Plugin `
        -Sandbox $sandbox
    Write-ODSPrivateEnvFile -Path $Paths.Config -Content ($config | ConvertTo-Json -Depth 20)
    $environment = [ordered]@{ GATEWAY_PORT = [string]$script:ODSDesktopPortalGatewayPort; EDGE_PORT = [string]$script:ODSDesktopPortalEdgePort
        INGRESS_PIPE = (Get-ODSDesktopPortalPipe) }
    foreach ($name in $secrets.Keys) { $environment[$name] = $secrets[$name] }
    Write-ODSPrivateEnvFile -Path $Paths.Environment -Content (ConvertTo-ODSDesktopEnvText $environment)
    Write-ODSPrivateEnvFile -Path $Paths.Intent -Content '{"State":"running"}'
    Register-ODSDesktopPortalTask $Paths $PythonDir
    Start-ScheduledTask -TaskName (Get-ODSDesktopPortalTaskName) -TaskPath '\'
    Wait-ODSDesktopPortalReady $Paths
    return [pscustomobject]@{ EdgeUrl = "http://127.0.0.1:$($script:ODSDesktopPortalEdgePort)"; EdgeKey = $secrets.PIXEL_OPENWEBUI_KEY }
}

function Remove-ODSDesktopPortal($Paths, [bool]$RemoveData, [string]$PythonDir) {
    <#
    .SYNOPSIS
        Uninstall: stop Portal, delete its task and files. The project
        workspace and chat history are the owner's data and stay unless
        RemoveData. Returns whether a task existed and what was kept.
    #>
    $taskName = Get-ODSDesktopPortalTaskName
    $task = @(Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue)
    if ($task.Count) { Disable-ScheduledTask -TaskName $taskName -TaskPath '\' | Out-Null }
    Stop-ODSDesktopPortal $Paths
    if ($task.Count) { Unregister-ScheduledTask -TaskName $taskName -TaskPath '\' -Confirm:$false }
    $python = Join-Path $PythonDir 'python.exe'
    if ((Test-Path -LiteralPath $Paths.SandboxRunner -PathType Leaf) -and (Test-Path -LiteralPath $python -PathType Leaf)) {
        & $python -I $Paths.SandboxRunner teardown --profile (Get-ODSDesktopPortalSandboxProfile) --workspace $Paths.Workspace | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "The Portal sandbox profile could not be removed (exit $LASTEXITCODE)." }
    }
    $kept = @()
    if (Test-Path -LiteralPath $Paths.Root) {
        $data = if ($RemoveData) { @() } else { @($Paths.Workspace, $Paths.ChatState) }
        foreach ($item in @(Get-ChildItem -LiteralPath $Paths.Root -Force)) {
            if ($data -contains $item.FullName) { $kept += $item.FullName } else { Remove-Item -LiteralPath $item.FullName -Recurse -Force }
        }
        if (-not $kept.Count) { Remove-Item -LiteralPath $Paths.Root -Recurse -Force }
    }
    return [pscustomobject]@{ Stopped = [bool]$task.Count; Kept = $kept }
}
