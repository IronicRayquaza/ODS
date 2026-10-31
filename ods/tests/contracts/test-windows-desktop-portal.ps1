# Windows Desktop Portal: the pins, the context budget and the OpenClaw
# configuration setup renders, the secrets it keeps across reruns, and the
# runtime files it copies. Only temporary files are written; nothing is
# downloaded, installed or started. Runs under Windows PowerShell 5.1 and
# PowerShell 7.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$lib = Join-Path $root 'installers/windows/lib'
$library = Join-Path $lib 'desktop-portal.ps1'
foreach ($name in @('private-file.ps1', 'native-llama-runtime.ps1', 'desktop-python.ps1', 'desktop-runtime.ps1', 'desktop-portal.ps1')) {
    . (Join-Path $lib $name)
}
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Get-Failure([scriptblock]$Action) {
    try { & $Action; return '' } catch { return $_.Exception.Message }
}

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-desktop-portal-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
try {
    # --- Pins: Node from the Desktop receipt, OpenClaw from the Pixel manifest ---
    $pins = Get-ODSDesktopPortalPins $root
    $manifest = [IO.File]::ReadAllText((Join-Path $root 'vendor/pixel/RELEASE-MANIFEST.json')) | ConvertFrom-Json
    Check ($pins.Node.version -eq $manifest.nodeRuntime.version) 'the Portal Node pin is the version the Pixel release manifest pins'
    Check ($pins.Node.url -ceq "https://nodejs.org/dist/v$($pins.Node.version)/node-v$($pins.Node.version)-win-x64.zip") 'the Portal Node pin is the official win-x64 zip'
    Check ($pins.OpenClaw.Version -eq $manifest.openclaw -and $pins.OpenClaw.Sha256 -eq $manifest.openclawPackage.sha256 -and
        $pins.OpenClaw.Integrity -eq $manifest.openclawPackage.integrity) 'OpenClaw comes from the release manifest with its SHA-256 and SHA-512'

    # --- Budget: the numbers pixel-runtime-budget.py applies on Linux ---
    $small = Get-ODSDesktopPortalBudget -Context 16384 -MaxTokens 4096 -ModelId 'Qwen3.5-4B-Q4_K_M.gguf'
    Check ($small.ReserveTokens -eq 6554 -and $small.KeepRecentTokens -eq 1024 -and $small.ToolResultMaxChars -eq 4096) 'a 16K route gets the Linux reserve, keep-recent and tool-result limits'
    Check ($small.Lean -and $small.BootstrapMaxChars -eq 2000 -and $small.BootstrapTotalMaxChars -eq 6000 -and $small.ContextInjection -eq 'never') 'a 16K route gets the lean prompt'
    Check ($small.QwenThinkingOff) 'a Qwen model is told explicitly not to think, matching llama-server reasoning off'
    $large = Get-ODSDesktopPortalBudget -Context 65536 -MaxTokens 8192 -ModelId 'gemma-4-12b-q4.gguf'
    Check (-not $large.Lean -and $large.BootstrapMaxChars -eq 14000 -and $large.ToolResultMaxChars -eq 16000 -and -not $large.QwenThinkingOff) 'a large context and model get the full prompt'
    Check ((Get-ODSDesktopPortalBudget -Context 65536 -MaxTokens 8192 -ModelId 'phi-3b-mini.gguf').Lean) 'a model of 4B parameters or fewer is lean at any context'
    $message = Get-Failure { $null = Get-ODSDesktopPortalBudget -Context 8192 -MaxTokens 2048 -ModelId 'x.gguf' }
    Check ($message -match 'at least 16384') 'a context below the Portal minimum is refused, not raised silently'

    # --- Config without the sandbox: no command can run ---
    $common = @{ Token = ('t' * 64); LlamaPort = 8080; LlamaKey = ('k' * 40); ModelId = 'Qwen3.5-4B-Q4_K_M.gguf'; Context = 16384
        Workspace = 'C:\Users\O''Brien\ODS portal\workspace'; PixelPlugin = 'C:\ods\runtime\pixel-agent\plugin' }
    $bare = New-ODSDesktopPortalConfig @common
    Check ($bare.gateway.bind -eq 'loopback' -and $bare.gateway.auth.mode -eq 'token' -and $bare.gateway.auth.token -eq ('t' * 64)) 'the gateway binds loopback with token auth'
    Check ($bare.gateway.http.endpoints.chatCompletions.enabled -eq $true) 'the OpenAI-compatible endpoint the ingress calls is on'
    $provider = $bare.models.providers['ods-local']
    Check ($provider.baseUrl -eq 'http://127.0.0.1:8080/v1' -and $provider.apiKey -eq ('k' * 40) -and $provider.api -eq 'openai-completions') 'the model route is the Desktop llama-server with its key'
    Check ($provider.timeoutSeconds -eq 1800) 'the provider timeout also lifts the 120 s idle watchdog for a slow CPU prompt'
    Check ($provider.models[0].contextWindow -eq 16384 -and $provider.models[0].maxTokens -eq 4096 -and $provider.models[0].reasoning -eq $false) 'the model limits come from the running model'
    Check ($bare.agents.defaults.sandbox.mode -eq 'off' -and $bare.tools.exec.host -eq 'gateway' -and $bare.tools.exec.security -eq 'deny') 'without the sandbox plugin every command is denied'
    Check (@($bare.plugins.allow).Count -eq 1 -and $bare.plugins.entries['pixel-ods'].hooks.allowConversationAccess -eq $true) 'only the ODS plugin loads, with its conversation hooks allowed'
    Check ($bare.agents.list[0].params.chat_template_kwargs.enable_thinking -eq $false) 'the Qwen thinking switch is off'

    # --- Config with the sandbox: every command runs in the AppContainer ---
    $sandbox = [pscustomobject]@{ Plugin = 'C:\ods\runtime\pixel-agent\windows\plugin'; Python = 'C:\ods\python\python.exe'
        Runner = 'C:\ods\runtime\pixel-agent\windows\sandbox_run.py'; Profile = (Get-ODSDesktopPortalSandboxProfile 'S-1-5-21-1-2-3-1001')
        ToolPath = 'C:\ods\node;C:\ods\python;C:\Windows\System32'; ControlRoot = 'C:\ods\home\.openclaw\.ods-exec-control' }
    $boxed = New-ODSDesktopPortalConfig @common -Sandbox $sandbox
    Check ($boxed.agents.defaults.sandbox.mode -eq 'all' -and $boxed.agents.defaults.sandbox.backend -eq 'ods-windows' -and $boxed.tools.exec.host -eq 'sandbox') 'with the sandbox plugin every command goes to the ods-windows backend'
    $entry = $boxed.plugins.entries['ods-windows-sandbox']
    Check ($entry.config.python -eq $sandbox.Python -and $entry.config.runner -eq $sandbox.Runner -and $entry.config.profile -eq $sandbox.Profile -and
        $entry.config.toolPath -eq $sandbox.ToolPath -and $entry.config.controlRoot -eq $sandbox.ControlRoot) 'the sandbox plugin receives every runner setting (a shadowed parameter once left them empty)'
    Check (@($boxed.plugins.allow) -contains 'ods-windows-sandbox' -and @($boxed.plugins.load.paths) -contains $sandbox.Plugin) 'the sandbox plugin is allowed and loaded from the runtime copy'
    $profileName = Get-ODSDesktopPortalSandboxProfile 'S-1-5-21-1-2-3-1001'
    $schema = [IO.File]::ReadAllText((Join-Path $root 'extensions/services/pixel-agent/windows/plugin/openclaw.plugin.json')) | ConvertFrom-Json
    Check ($profileName -cmatch $schema.configSchema.properties.profile.pattern) 'the AppContainer profile name matches the plugin config schema'
    Check ($profileName -ceq (Get-ODSDesktopPortalSandboxProfile 'S-1-5-21-1-2-3-1001') -and $profileName -cne (Get-ODSDesktopPortalSandboxProfile 'S-1-5-21-1-2-3-1002')) 'each Windows account gets its own AppContainer'

    # --- The JSON setup writes keeps every list a list ---
    $parsed = ($boxed | ConvertTo-Json -Depth 20) | ConvertFrom-Json
    Check (@($parsed.plugins.allow).Count -eq 2 -and $parsed.agents.list.Count -eq 1 -and $parsed.models.providers.'ods-local'.models[0].input -is [array]) 'single-item lists survive ConvertTo-Json in both PowerShell editions'
    Check ($parsed.agents.defaults.workspace -ceq $common.Workspace) 'a workspace path with spaces and an apostrophe survives as written'
    # 5.1 refuses a splatted key passed again, so the bad name goes in a copy.
    $badName = @{} + $common
    $badName.ModelId = 'model; rm -rf /'
    $message = Get-Failure { $null = New-ODSDesktopPortalConfig @badName }
    Check ($message -match 'cannot be used') 'a model name that is not a plain file name is refused'

    # --- Secrets: kept across reruns, replaced when damaged ---
    $paths = Get-ODSDesktopPortalPaths (Join-Path $fixture 'Portal [QA] O''Brien')
    $null = New-Item -ItemType Directory -Force -Path $paths.Root
    $first = Read-ODSDesktopPortalSecrets $paths
    Check (@($first.Keys).Count -eq 3 -and @($first.Values | Where-Object { $_ -cmatch '^[A-Za-z0-9_-]{32,128}$' }).Count -eq 3) 'a first install makes three strong keys'
    Check (@($first.Values | Sort-Object -Unique).Count -eq 3) 'the gateway token, edge key and preview key are distinct'
    Write-ODSPrivateEnvFile -Path $paths.Environment -Content (ConvertTo-ODSDesktopEnvText $first)
    $again = Read-ODSDesktopPortalSecrets $paths
    Check ($again.GATEWAY_TOKEN -ceq $first.GATEWAY_TOKEN -and $again.PIXEL_OPENWEBUI_KEY -ceq $first.PIXEL_OPENWEBUI_KEY) 'a rerun keeps the saved keys'
    $damaged = [ordered]@{ GATEWAY_TOKEN = 'short'; PIXEL_OPENWEBUI_KEY = $first.PIXEL_OPENWEBUI_KEY; PIXEL_PREVIEW_PROXY_KEY = $first.PIXEL_PREVIEW_PROXY_KEY }
    Write-ODSPrivateEnvFile -Path $paths.Environment -Content (ConvertTo-ODSDesktopEnvText $damaged)
    $repaired = Read-ODSDesktopPortalSecrets $paths
    Check ($repaired.GATEWAY_TOKEN -cne 'short' -and $repaired.GATEWAY_TOKEN.Length -ge 32 -and $repaired.PIXEL_OPENWEBUI_KEY -ceq $first.PIXEL_OPENWEBUI_KEY) 'a damaged key is replaced and the good ones stay'

    # --- dashboard-api gets the edge origin and key only when Portal runs ---
    $desktop = Get-ODSDesktopPaths (Join-Path $fixture 'desktop')
    $plain = New-ODSDesktopApiEnvironment $desktop 3101 8080 'C:\runtime' ('a' * 40)
    Check (-not $plain.Contains('PIXEL_EDGE_URL') -and -not $plain.Contains('PIXEL_OPENWEBUI_KEY')) 'without Portal the app gets no edge settings'
    $withPortal = New-ODSDesktopApiEnvironment $desktop 3101 8080 'C:\runtime' ('a' * 40) ([pscustomobject]@{ EdgeUrl = 'http://127.0.0.1:9595'; EdgeKey = ('e' * 40) })
    Check ($withPortal.PIXEL_EDGE_URL -eq 'http://127.0.0.1:9595' -and $withPortal.PIXEL_OPENWEBUI_KEY -eq ('e' * 40)) 'with Portal the app gets the loopback edge origin and key'

    # --- Runtime copy: what the task runs, never a checkout or tests ---
    Publish-ODSDesktopPortalRuntime $root $paths
    foreach ($relative in @('pixel-agent\windows\portal_supervisor.py', 'pixel-agent\windows\sandbox_run.py', 'pixel-agent\windows\fs_op.py',
        'pixel-agent\windows\win_job.py', 'pixel-agent\windows\plugin\index.js', 'pixel-agent\windows\plugin\cancellable.mjs',
        'pixel-agent\windows\plugin\openclaw.plugin.json', 'pixel-agent\host\windows_ingress.mjs', 'pixel-agent\host\pixel_ingress.mjs',
        'pixel-agent\plugin\index.js', 'pixel-agent\plugin\openclaw.plugin.json', 'pixel-edge\pixel_edge.py')) {
        Check (Test-Path -LiteralPath (Join-Path $paths.Runtime $relative) -PathType Leaf) "the runtime copy has $relative"
    }
    Check (-not (Test-Path -LiteralPath (Join-Path $paths.Runtime 'pixel-agent\tests'))) 'the runtime copy has no tests'

    # --- Static rules for Windows PowerShell 5.1 ---
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($library, [ref]$tokens, [ref]$errors)
    Check ($errors.Count -eq 0) 'desktop-portal.ps1 parses without errors'
    $pathCommands = 'Test-Path', 'Get-Item', 'Get-ChildItem', 'Remove-Item', 'Move-Item', 'Copy-Item', 'Get-Content', 'Set-Content', 'Add-Content'
    $wildcard = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -in $pathCommands -and
        -not ($node.CommandElements | Where-Object { $_ -is [Management.Automation.Language.CommandParameterAst] -and $_.ParameterName -eq 'LiteralPath' })
    }, $true))
    Check ($wildcard.Count -eq 0) 'every path cmdlet uses -LiteralPath'
    $automatic = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left -is [Management.Automation.Language.VariableExpressionAst] -and
        $node.Left.VariablePath.UserPath -in @('profile', 'home', 'input', 'args', 'error', 'host', 'pid', 'pwd', 'matches', 'event')
    }, $true))
    Check ($automatic.Count -eq 0) 'no automatic variable is assigned'
    Check (-not ([IO.File]::ReadAllBytes($library) | Where-Object { $_ -gt 127 })) 'desktop-portal.ps1 is ASCII-only for Windows PowerShell 5.1'
} finally {
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
Microsoft.PowerShell.Utility\Write-Host "All $script:checks Windows Desktop Portal checks passed."
