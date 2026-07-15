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
        throw "未找到 $Name。$InstallHint"
    }
    return $command.Source
}

function Invoke-Codex {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

    & $script:CodexPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Codex 命令执行失败（退出码 $LASTEXITCODE）：codex $($Arguments -join ' ')"
    }
}

$script:CodexPath = Require-Command -Name "codex" -InstallHint "请先安装或更新 Codex 桌面版：https://openai.com/codex/"
$null = Require-Command -Name "git" -InstallHint "请先安装 Git：https://git-scm.com/downloads"

Write-Host "正在添加公开插件源：$Repository"
& $script:CodexPath plugin marketplace add $Repository --ref main --json
if ($LASTEXITCODE -ne 0) {
    Write-Host "插件源可能已经添加，正在刷新：$Marketplace"
    Invoke-Codex plugin marketplace upgrade $Marketplace
}

Write-Host "正在安装插件：$Plugin@$Marketplace"
Invoke-Codex plugin add "$Plugin@$Marketplace" --json

Write-Host ""
Write-Host "安装完成。请关闭当前任务，并在 Codex 中新建任务后使用插件。" -ForegroundColor Green
Write-Host "推荐首条提示词：使用 `$mining-rock-co2-paper-lifecycle 创建或接管一个论文项目。"
