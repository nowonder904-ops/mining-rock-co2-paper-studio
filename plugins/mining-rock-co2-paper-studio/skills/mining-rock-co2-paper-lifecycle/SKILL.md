---
name: mining-rock-co2-paper-lifecycle
description: 面向采矿工程、岩石力学和 CO₂ 地质封存论文的独立全生命周期工作流。用于研究问题与创新核验、近两年及经典文献检索、合法全文获取与人工补全暂停、Mode B 人工分级精读、Obsidian 证据库、文献综述、论文骨架与正文、数据图、机制图提示词、学术润色、引用和一致性审计、投稿交付及审稿回复；也用于从 pipeline_state.yaml 恢复中断项目。插件内置全部阶段合同和确定性脚本，不依赖或安装其他全局 SKILL。仅在这三个学科及其交叉范围内触发。
---

# 采矿—岩石力学—CO₂ 论文全流程

<!-- 使用场景：当采矿工程、岩石力学或 CO₂ 地质封存论文需要从开题、文献证据库一路推进到成稿、投稿与修回时使用。 -->

## 核心原则

把模型的通用研究、推理和写作能力用于语义工作，把不可猜测的状态、文件、统计、引用、图件和交付检查交给插件脚本。不要查找、安装或调用任何其他全局 SKILL；外部网络、数据库权限、PDF 解析器、绘图库和文档转换器只作为运行环境能力，并由 preflight 明确报告。

每轮只推进一个可验收阶段。产物存在不等于完成；结构、证据、边界和人工闸门全部通过后，才可更新状态。

## 启动与恢复

1. 从本文件向上两级解析插件根目录；脚本均位于 `<plugin-root>/scripts/`，不依赖当前工作目录。
2. 运行 `runtime_preflight.py`。核心状态与知识库功能只需要 Python 3 标准库；联网检索、PDF 文本提取、投稿级绘图和 PDF 导出按报告逐项启用。缺少某项时只阻塞对应阶段，不伪装为成功。
3. 若用户未提供论文库路径，只做只读规划。新建论文库前必须获得“文件夹名称 + 父目录绝对路径”两个明确输入；不得猜测路径。
4. 已有库先运行 `pipeline_status.py`，从 `00_Control/pipeline_state.yaml` 恢复，不重复已验收阶段。若 schema 低于 2，先运行 `migrate_vault.py --vault <论文库>` 展示 dry-run；得到用户确认后才加 `--apply`，脚本会备份旧控制文件并只补缺失模板。
5. 读取 `references/stage-contracts.md` 与 `references/quality-gates.md`；随后只读取当前阶段对应的参考文件，不一次加载全部资料。

## 按需路由

| 当前阶段 | 读取 | 确定性入口 |
|---|---|---|
| S0–S2 | `domain-evidence-rules.md`、`research-search-playbook.md` | `stage_control.py`、`validate_stage_outputs.py` |
| S3–S5 | `research-search-playbook.md`、`manual-fulltext-gate.md` | `scholarly_search.py`、`literature_register.py`、`fulltext_fetch.py`、`download_failure_gate.py` |
| S5.5–S7 | `mode-b-selection.md`、`evidence-vault-playbook.md`、`obsidian-vault-contract.md` | `mode_b_manager.py`、`evidence_vault.py`、`validate_obsidian_assets.py` |
| S8–S11 | `manuscript-playbook.md`、`domain-evidence-rules.md` | `build_manuscript_context.py`、`manuscript_guard.py` |
| S10.5 | `figures-playbook.md` | `figure_engine.py`、`mechanism_figure.py`、`validate_figure_stage.py` |
| S12–S14 | `review-delivery-playbook.md`、`runtime-boundaries.md` | `claim_evidence_audit.py`、`delivery_build.py`、`review_response_audit.py` |

## 三个人工闸门

### G1：论文库位置

未确认文件夹名称和父目录绝对路径前，禁止创建目录、移动文献或初始化 Obsidian 文件。

### G2：自动下载失败后的人工补全文

S4 每次自动下载后运行 `download_failure_gate.py build`。只要返回 `blocked_by_user`：

- 展示 `download_failures.md` 和 `Manual_Inbox` 的绝对路径；
- 停在 S4.5，不进入筛选、分级或精读；
- 不轮询、不自动重试，等待用户明确通知人工下载完成。

收到通知后运行 `verify`。结构检查不等于身份正确；必须核对题名及 DOI，或在 DOI 不显示时用题名加作者、期刊或年份交叉验证，再登记 `confirm-identity`。仅当全部记录 verified，或由用户明确排除并给出原因，才可进入 S5。不得用摘要、网页片段或搜索摘要冒充全文。

### G3：Mode B 人工分级

去重和全文核验后，用 `mode_b_manager.py prepare` 生成建议表，并询问用户确认 A/B/C/D 定义、逐篇等级、顺序、批量上限和逐图逐表要求。建议不能代替用户决定；确认文件未通过 `mode_b_manager.py validate` 前保持 `blocked_by_user`，不得开始精读。

## 阶段

| 阶段 | 目标 | 必需结果 |
|---|---|---|
| S0–S1 | 配置、初始化或接管 | manifest、state、任务板、决策与产物账册 |
| S2 | RQ、创新与预审质疑 | RQ 卡、创新矩阵、方法可回答性与最强反对意见 |
| S3–S4 | 可复现检索与合法全文获取 | 检索策略/日志、候选表、去重记录、全文状态 |
| S4.5 | 人工补齐下载失败全文 | 失败清单、用户通知、PDF 结构与身份核验 |
| S5–S5.5 | 分类归档与人工分级 | 文献登记表、主题目录、用户确认的 Mode B 批次 |
| S6 | 全文精读与证据回写 | 证据账本、引用卡、阅读笔记、Wiki、索引和地图 |
| S7–S8 | 综述、空白、贡献与边界 | 研究脉络、争议、可证空白、确认的贡献和不可声称项 |
| S9–S10 | 论文骨架与逐节正文 | 主张—证据—图表主线、可复现方法、章节草稿与质量报告 |
| S10.5 | 数据图及机制图规划 | 投稿级数据图；机制图详细提示词、证据映射和正文占位 |
| S11 | 学术润色与作者声纹校准 | 语言版本、修改记录和科学不变量比对 |
| S12 | 引用、完整性与模拟审稿 | 证据闭环、跨文件一致性、P0/P1 整改 |
| S13 | 定稿与投稿交付 | Word、LaTeX、可用时的 PDF、数据可用性与哈希清单 |
| S14 | 审稿回复 | 稳定评论编号、逐点回复、修改位置、修订稿和复审记录 |

## 执行协议

1. 用 `stage_control.py begin` 记录本阶段输入和当前事实快照。
2. 依据当前 playbook 完成语义工作；未知事实用显式占位或写入 `Evidence_Gaps.md`，不得补造。
3. 运行相应确定性脚本并保存 JSON/CSV/Markdown 报告。脚本返回 `blocked_by_user`、`blocked_by_evidence` 或 `blocked_by_runtime` 时停止。
4. 运行 `validate_stage_outputs.py --stage <Sx>`；S10.5、S11、S12、S13、S14 还必须通过各自专用验证器。
5. 只有全部验证退出 0，才运行 `stage_control.py complete`。随后汇报：已完成、可核查证据、未解决风险、下一步和需用户确认事项。

## 科学与写入边界

- 只处理采矿工程、岩石力学、CO₂ 地质封存及其交叉；其他主题说明领域校验不足。
- 区分实验、理论、数值模拟、现场监测和类比证据；区分孔隙/试样/工程/储层尺度、短期/长期与相关/因果。
- 文献、DOI、数据、公式参数、统计量、图件和结果不得编造。网页可作线索，metadata-only 不可作正文证据。
- 润色和自然化不得改变数值、方向、单位、符号、限定词、图表号、引文键或证据强度；不承诺“规避 AI 检测”。
- 只创建缺失文件；更新前读取并保留用户字段、手工链接与权威内容。冲突先写入决策账本，不静默覆盖。
- 不自动安装系统软件、Python 包、MCP、浏览器扩展或凭据；需要新增运行环境时说明用途并取得用户授权。

## 完成判据

只有当阶段必需产物通过结构检查、关键主张可追溯、跨文件一致、结论未越界、人工闸门有记录时，才标记 `completed`。S13 还要求题名、RQ、术语、单位、图号、结论强度、引用和交付哈希一致；S14 仅在收到真实编辑或审稿意见后启用。
