# Windows Desktop native apps: folders, task names, the download pin and the
# launch file setup builds from a manifest recipe, and what uninstall keeps.
# Nothing is downloaded, installed or started; only temporary folders are
# written. The recipe is resolved by Python with PyYAML when one is available.
# Runs under Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$lib = Join-Path $root 'installers/windows/lib'
$library = Join-Path $lib 'desktop-apps.ps1'
foreach ($name in @('private-file.ps1', 'native-llama-runtime.ps1', 'desktop-python.ps1', 'desktop-runtime.ps1', 'desktop-portal.ps1', 'desktop-apps.ps1')) {
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

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-desktop-apps-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
try {
    # --- Folders and task names ---
    $paths = Get-ODSDesktopExtensionPaths 'qdrant' (Join-Path $fixture 'apps [QA] O''Brien')
    Check ($paths.Bin.EndsWith('qdrant\bin') -and $paths.Data.EndsWith('qdrant\data') -and $paths.Launch.EndsWith('qdrant\launch.json')) 'each app has its own bin, data and launch file'
    foreach ($bad in @('..', 'Qdrant', 'a/b', 'a\b', '', ('x' * 42))) {
        Check ((Get-Failure { $null = Get-ODSDesktopExtensionPaths $bad $fixture }) -match 'not an ODS app id') "the app id '$bad' is refused"
    }
    Check ((Get-ODSDesktopExtensionTaskName 'qdrant' 'S-1-5-21-1-2-3-1001') -ceq 'ODSApp-qdrant-S-1-5-21-1-2-3-1001') 'the sign-in task is per app and per Windows account'

    # --- The download pin Save-ODSDesktopPinnedFile checks ---
    $recipe = [pscustomobject]@{ id = 'qdrant'; name = 'Qdrant (Vector DB)'; version = '1.16.3'; kind = 'zip'; file = 'qdrant-x86_64-pc-windows-msvc.zip'
        url ='https://github.com/qdrant/qdrant/releases/download/v1.16.3/qdrant-x86_64-pc-windows-msvc.zip'; size = 27008258
        sha256 = 'a1159282922776a05bdeaad9e90c85d8d1ca0bc90d9e5e4add56133e15748753'; executable = 'qdrant.exe'; args = @()
        env = [pscustomobject]@{ QDRANT__SERVICE__HOST = '127.0.0.1'; QDRANT__SERVICE__HTTP_PORT = '6333' }; port = 6333; health = '/healthz' }
    $pin = New-ODSDesktopExtensionPin $recipe
    Check ($pin.file -ceq 'qdrant-x86_64-pc-windows-msvc.zip' -and $pin.size -eq 27008258 -and $pin.sha256 -ceq $recipe.sha256) 'the pin carries the recipe file name, size and SHA-256'
    $message = Get-Failure { Assert-ODSDesktopDownloadPin $pin 'Qdrant' '^https://github\.com/qdrant/qdrant/releases/download/' }
    Check ($message -eq '') 'the pin passes the same checks as the Desktop Python pin'

    # --- launch.json for the supervisor ---
    $launch = New-ODSDesktopExtensionLaunch $recipe $paths
    Check ($launch.executablePath -ceq (Join-Path $paths.Bin 'qdrant.exe') -and $launch.port -eq 6333 -and $launch.health -ceq '/healthz') 'the launch file names the unpacked executable, port and health path'
    Check ($launch.env.QDRANT__SERVICE__HOST -eq '127.0.0.1' -and $launch.data -ceq $paths.Data) 'the launch file keeps the loopback setting and the data folder'
    $json = ($launch | ConvertTo-Json -Depth 6) | ConvertFrom-Json
    Check ($json.args -is [array] -or $null -eq $json.args -or @($json.args).Count -eq 0) 'an empty argument list stays a list'
    Check ((New-ODSDesktopExtensionLaunch $recipe $paths 'C:\elsewhere\llama-server.exe').executablePath -ceq (Join-Path $paths.Bin 'qdrant.exe')) 'a zip app never runs the llama-server it is handed'

    # --- The llama-server kind: Desktop's own llama-server, a pinned model, a key ---
    $embedPaths = Get-ODSDesktopExtensionPaths 'embeddings' (Join-Path $fixture 'apps [QA] O''Brien')
    $llama = [pscustomobject]@{ id = 'embeddings'; name = 'TEI (Embeddings)'; version = '1.5'; kind = 'llama-server'; file = 'bge-base-en-v1.5-q8_0.gguf'
        url = 'https://huggingface.co/CompendiumLabs/bge-base-en-v1.5-gguf/resolve/24914efdfa9ee54e815c3fcaa78a617031251c5c/bge-base-en-v1.5-q8_0.gguf'
        size = 117974304; sha256 = 'ad1afe72cd6654a558667a3db10878b049a75bfd72912e1dabb91310d671173c'; executable = $null
        args = @('-m', (Join-Path $embedPaths.Bin 'bge-base-en-v1.5-q8_0.gguf'), '--api-key-file', (Join-Path $embedPaths.Data 'api-key'))
        env = [pscustomobject]@{}; port = 8090; health = '/health' }
    Check ((New-ODSDesktopExtensionPin $llama).file -ceq 'bge-base-en-v1.5-q8_0.gguf') 'the model pin is named after the model file'
    $engine = Join-Path $fixture 'llama.cpp\b9014\llama-server.exe'
    $llamaLaunch = New-ODSDesktopExtensionLaunch $llama $embedPaths $engine
    Check ($llamaLaunch.executablePath -ceq $engine -and $llamaLaunch.port -eq 8090) 'a llama-server app runs the llama-server setup hands it'
    Check ((Get-Failure { $null = New-ODSDesktopExtensionLaunch $llama $embedPaths '' }) -match 'did not find it') 'a llama-server app without a llama-server is refused'
    $null = New-Item -ItemType Directory -Force -Path $embedPaths.Data
    New-ODSDesktopExtensionKey $embedPaths
    $keyFile = Join-Path $embedPaths.Data 'api-key'
    $key = [IO.File]::ReadAllText($keyFile)
    Check ($key -cmatch '^ods-[0-9a-f]{64}$') 'setup writes a random key, one line, no newline'
    New-ODSDesktopExtensionKey $embedPaths
    Check ([IO.File]::ReadAllText($keyFile) -ceq $key) 'a rerun keeps the existing key'
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        $rules = @((Get-Acl -LiteralPath $keyFile).Access)
        Check ((Get-Acl -LiteralPath $keyFile).AreAccessRulesProtected -and -not @($rules | Where-Object { [string]$_.IdentityReference -match 'Users|Everyone|Authenticated' }).Count) 'the key file is owner-only'
    }

    # --- A repair waits for its own app to let go of the port (live 2026-11-01) ---
    $script:listenChecks = 0
    function Get-ScheduledTask { [pscustomobject]@{ TaskName = 'ODSApp-embeddings-S-1'; State = 'Running' } }
    function Stop-ScheduledTask { }
    function Get-NetTCPConnection { $script:listenChecks++; if ($script:listenChecks -le 3) { [pscustomobject]@{ LocalAddress = '127.0.0.1' } } }
    Stop-ODSDesktopExtension 'embeddings' 8090
    Check ($script:listenChecks -eq 4) 'stopping an app waits until its port is free'
    $script:listenChecks = 0
    Stop-ODSDesktopExtension 'embeddings'
    Check ($script:listenChecks -eq 0) 'uninstall, which passes no port, does not wait on one'
    Remove-Item function:Get-ScheduledTask, function:Stop-ScheduledTask, function:Get-NetTCPConnection

    # --- Which apps a setup rerun repairs ---
    $repairRoot = Join-Path $fixture 'repair-apps'
    foreach ($id in @('qdrant', 'embeddings', 'half-done')) { $null = New-Item -ItemType Directory -Force -Path (Join-Path $repairRoot "$id\bin") }
    foreach ($id in @('qdrant', 'embeddings')) { [IO.File]::WriteAllText((Join-Path $repairRoot "$id\bin\ods-app.json"), '{}') }
    $installed = @(Get-ODSDesktopInstalledExtensions $repairRoot | Sort-Object)
    Check (($installed -join ',') -ceq 'embeddings,qdrant') 'a rerun repairs the apps that finished installing, and no others'
    Check (@(Get-ODSDesktopInstalledExtensions (Join-Path $fixture 'none')).Count -eq 0) 'no apps folder means nothing to repair'

    # --- The real recipe, resolved by the shared Python reader ---
    $python = @(Get-Command python -CommandType Application -ErrorAction SilentlyContinue)
    $hasYaml = $python.Count -and ((& $python[0].Source -c 'import yaml' 2>$null) -or $LASTEXITCODE -eq 0)
    if ($hasYaml) {
        $resolved = Get-ODSDesktopExtensionRecipe (Join-Path $root 'extensions/services/dashboard-api') (Join-Path $root 'extensions/services') `
            (Split-Path -Parent $python[0].Source) 'qdrant' $paths
        Check ($resolved.env.QDRANT__STORAGE__STORAGE_PATH -ceq (Join-Path $paths.Data 'storage')) 'the shipped Qdrant recipe resolves its storage into the app data folder'
        $embed = Get-ODSDesktopExtensionRecipe (Join-Path $root 'extensions/services/dashboard-api') (Join-Path $root 'extensions/services') `
            (Split-Path -Parent $python[0].Source) 'embeddings' $embedPaths
        $embedArgs = @($embed.args)
        Check ($embed.kind -ceq 'llama-server' -and $embedArgs[[array]::IndexOf($embedArgs, '-m') + 1] -ceq (Join-Path $embedPaths.Bin $embed.file)) 'the shipped embeddings recipe loads its model from the app bin folder'
        Check ($embedArgs[[array]::IndexOf($embedArgs, '--api-key-file') + 1] -ceq (Join-Path $embedPaths.Data 'api-key')) 'the shipped embeddings recipe reads the key setup writes'
        Check ((Get-Failure { $null = Get-ODSDesktopExtensionRecipe (Join-Path $root 'extensions/services/dashboard-api') (Join-Path $root 'extensions/services') (Split-Path -Parent $python[0].Source) 'no-such-app' $paths }) -match 'no usable native Windows recipe') 'an app without a recipe is refused'
    } else {
        Microsoft.PowerShell.Utility\Write-Host 'SKIP recipe resolution: no Python with PyYAML on PATH'
    }

    # --- Uninstall keeps data unless asked ---
    $appsRoot = Join-Path $fixture 'uninstall-apps'
    foreach ($id in @('qdrant', 'n8n')) {
        $p = Get-ODSDesktopExtensionPaths $id $appsRoot
        foreach ($folder in @($p.Bin, $p.Data, $p.Logs)) { $null = New-Item -ItemType Directory -Force -Path $folder }
        [IO.File]::WriteAllText((Join-Path $p.Data 'collection.db'), 'owner data')
    }
    $kept = @(Remove-ODSDesktopExtensions $false $appsRoot)
    Check ($kept.Count -eq 2 -and (Test-Path -LiteralPath (Join-Path $appsRoot 'qdrant\data\collection.db')) -and -not (Test-Path -LiteralPath (Join-Path $appsRoot 'qdrant\bin'))) 'uninstall removes app files and keeps app data by default'
    $null = Remove-ODSDesktopExtensions $true $appsRoot
    Check (-not (Test-Path -LiteralPath $appsRoot)) 'uninstall with data removal leaves no apps folder'

    # --- Static rules for Windows PowerShell 5.1 ---
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($library, [ref]$tokens, [ref]$errors)
    Check ($errors.Count -eq 0) 'desktop-apps.ps1 parses without errors'
    $pathCommands = 'Test-Path', 'Get-Item', 'Get-ChildItem', 'Remove-Item', 'Move-Item', 'Copy-Item', 'Get-Content', 'Set-Content', 'Add-Content'
    $wildcard = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -in $pathCommands -and
        -not ($node.CommandElements | Where-Object { $_ -is [Management.Automation.Language.CommandParameterAst] -and $_.ParameterName -eq 'LiteralPath' })
    }, $true))
    Check ($wildcard.Count -eq 0) 'every path cmdlet uses -LiteralPath'
    $automatic = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left -is [Management.Automation.Language.VariableExpressionAst] -and
        $node.Left.VariablePath.UserPath -in @('env', 'profile', 'home', 'input', 'args', 'error', 'host', 'pid', 'pwd', 'matches', 'event')
    }, $true))
    Check ($automatic.Count -eq 0) 'no automatic variable is assigned'
    Check (-not ([IO.File]::ReadAllBytes($library) | Where-Object { $_ -gt 127 })) 'desktop-apps.ps1 is ASCII-only for Windows PowerShell 5.1'
} finally {
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
Microsoft.PowerShell.Utility\Write-Host "All $script:checks Windows Desktop app checks passed."
