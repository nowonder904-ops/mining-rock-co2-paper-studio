# 采矿·岩石力学·CO₂ 论文工作室

面向采矿工程、岩石力学和 CO₂ 地质封存论文的 Codex 独立全生命周期插件。它将研究问题核验、文献检索、全文补全暂停、Mode B 分级精读、Obsidian 证据库、综述、论文骨架与正文、图件、质量审计、投稿和审稿回复串成可恢复流程。

插件已内置所需阶段合同、模板和确定性脚本，不会安装或依赖作者电脑上的旧版全局 SKILL。

## Windows 快速安装

前提：已经安装最新版 Codex 桌面版、Git，并且 `codex` 与 `git` 命令可用。

在 PowerShell 中粘贴这一条命令：

```powershell
$p="$env:TEMP\install-mining-rock-co2-paper-studio.ps1"; Invoke-WebRequest "https://raw.githubusercontent.com/nowonder904-ops/mining-rock-co2-paper-studio/main/install.ps1" -OutFile $p; powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p
```

也可以下载仓库 ZIP，解压后双击 `install.cmd`。安装脚本只会添加本仓库的 Codex marketplace，并安装 `mining-rock-co2-paper-studio` 插件；可先阅读 [install.ps1](./install.ps1) 再运行。

## 手动安装

```powershell
codex plugin marketplace add nowonder904-ops/mining-rock-co2-paper-studio --ref main
codex plugin add mining-rock-co2-paper-studio@nowonder-academic
```

安装完成后，新建一个 Codex 任务，然后输入：

```text
使用 $mining-rock-co2-paper-lifecycle 创建或接管一个论文项目。
```

## 工作流中的人工闸门

- 开始时询问论文数据库名称和父目录绝对路径，不继承作者电脑的盘符。
- 文献自动下载失败时输出失败清单并暂停，等待用户手动补全后再继续。
- 进入 Mode B 精读前询问文献分级，不替用户擅自确定精读层级。
- 各阶段通过 `pipeline_state.yaml` 保存状态，可在新任务中从断点恢复。

## 适用范围

只面向以下学科及其交叉研究：

- 采矿工程
- 岩石力学与岩石工程
- CO₂ 地质封存

## 运行环境

- 必需：Codex、Git、Python 3.10 或更高版本。
- 推荐：Obsidian，用于浏览证据库、Wiki、Bases 与 Canvas。
- 可选：Microsoft Word、LaTeX、OriginPro 及科研绘图库；缺少时预检会说明受影响的阶段。

## 更新

```powershell
codex plugin marketplace upgrade nowonder-academic
codex plugin add mining-rock-co2-paper-studio@nowonder-academic
```

更新或首次安装后都应新建 Codex 任务，使新版本的技能与脚本生效。

## 数据与隐私

公开仓库不包含作者的论文、数据库、本机路径、账号密钥或个人配置。插件只在用户明确指定的论文库中创建和更新文件；联网文献检索与合法全文获取会访问相应公开学术服务。
