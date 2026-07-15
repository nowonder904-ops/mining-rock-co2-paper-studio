[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Repository = "nowonder904-ops/mining-rock-co2-paper-studio"
$Marketplace = "nowonder-academic"
$Plugin = "mining-rock-co2-paper-studio"

function Require-Command {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$InstallHint
    )

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "$Name was not found. $InstallHint"
    }
    return $command.Source
}

function Invoke-Codex {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

    & $script:CodexPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        $exitCode = $LASTEXITCODE
        throw "Codex command failed (exit code $exitCode): codex $($Arguments -join ' ')"
    }
}

$script:CodexPath = Require-Command -Name "codex" -InstallHint "Install or update Codex first: https://openai.com/codex/"
$null = Require-Command -Name "git" -InstallHint "Install Git first: https://git-scm.com/downloads"

if ($env:CODEX_HOME -and -not (Test-Path -LiteralPath $env:CODEX_HOME)) {
    New-Item -ItemType Directory -Path $env:CODEX_HOME -Force | Out-Null
}

# Keep Git's Windows long-path support local to this installer process.
$gitConfigIndex = 0
if ($env:GIT_CONFIG_COUNT -match '^\d+$') {
    $gitConfigIndex = [int]$env:GIT_CONFIG_COUNT
}
[Environment]::SetEnvironmentVariable("GIT_CONFIG_KEY_$gitConfigIndex", "core.longpaths", "Process")
[Environment]::SetEnvironmentVariable("GIT_CONFIG_VALUE_$gitConfigIndex", "true", "Process")
$env:GIT_CONFIG_COUNT = [string]($gitConfigIndex + 1)

$marketplaceJson = & $script:CodexPath plugin marketplace list --json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read configured Codex marketplaces."
}
$marketplaceState = ($marketplaceJson -join [Environment]::NewLine) | ConvertFrom-Json
$marketplaceExists = @($marketplaceState.marketplaces | ForEach-Object { $_.name }) -contains $Marketplace

if ($marketplaceExists) {
    Write-Host "Refreshing marketplace: $Marketplace"
    Invoke-Codex plugin marketplace upgrade $Marketplace
} else {
    Write-Host "Adding public plugin marketplace: $Repository"
    Invoke-Codex plugin marketplace add $Repository --ref main --json
}

Write-Host "Installing plugin: $Plugin@$Marketplace"
Invoke-Codex plugin add "$Plugin@$Marketplace" --json

Write-Host ""
Write-Host "Installation complete. Start a new Codex task before using the plugin." -ForegroundColor Green
Write-Host 'Suggested prompt: Use $mining-rock-co2-paper-lifecycle to create or resume a paper project.'
