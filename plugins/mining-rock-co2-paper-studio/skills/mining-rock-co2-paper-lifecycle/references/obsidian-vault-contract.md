# Obsidian 论文库合同

## 实际使用的语法子集

知识库文件由插件直接读写，不依赖 Obsidian 或其 CLI。Markdown 使用 UTF-8、YAML properties、`[[wikilinks]]`、`![[embeds]]`、callouts、MathJax 和 HTML 注释中的自动区块标记。缺失值保持为空或显式 gap，不猜测。

每篇文献笔记至少包含：`paper_id`、`title`、`year`、`doi`、`source_type`、`domain`、`topics`、`fulltext_status`、`mode_b_grade`、`reading_status`、`evidence_status`、`pdf_path`、`created`、`updated`。

`.base` 只维护三类固定视图：文献总览、Mode B 进度、证据缺口。`.canvas` 只维护研究问题、机制—证据、主张—引用三类地图。Canvas 节点/边 ID 必须唯一，边端点必须存在；自动节点使用 `auto-` 前缀，更新时只替换同一论文的自动节点。

## 写入安全

- 只创建缺失目录和文件；更新前读取现有内容。
- 自动内容放在 `<!-- AUTO:<key>:BEGIN/END -->` 内；区块外的用户内容、properties 和手工链接不覆盖。
- 移动 PDF 前核验来源、目标、hash、paper_id 和回链；不同内容的同名文件必须阻塞。
- 网页内容只标记 `web-lead`，不能把全文或精读状态推进到完成。
- S6 和 S14 运行 `validate_obsidian_assets.py`；即使 Obsidian 未安装，离线结构检查也必须通过。

Obsidian CLI 若存在，只用于可选的打开、搜索和渲染烟雾测试，不是核心流程前置条件。
