# Mode B 人工分级合同

## 默认定义

- A：直接决定研究问题、核心机制或主要方法；完整精读并逐图逐表核查。
- B：强支撑核心主张或关键方法；完整精读，按相关性核查图表。
- C：提供背景、参数范围或对照；结构化阅读并提取带原文定位的证据。
- D：低相关、重复、全文不可核验或不满足纳排标准；不精读并保留理由。

## 必须暂停询问

运行 `python scripts/mode_b_manager.py prepare --vault <论文库>`，向用户展示候选表和模型的建议，然后明确询问：

“请确认本批 Mode B 的 A/B/C/D 定义与逐篇等级、精读顺序、批量上限，以及是否要求逐图逐表分析；在你确认前我不会进入精读。”

建议不能替代用户决定。只接受用户明确回复后形成的决策表；不得从阅读时长、引用数或模型自信度自动代填。

## 记录与验证

把用户决定保存为含 `paper_id`、`confirmed_grade`、`order` 和 `note` 的 CSV，然后运行：

```bash
python scripts/mode_b_manager.py confirm --vault <论文库> --decisions <用户确认表> --batch-limit <N> --figure-table-level <none|relevant|all> --definitions-mode <default|custom> --definitions-note <说明>
python scripts/mode_b_manager.py validate --vault <论文库>
```

配置还必须记录 `batch_id`、`confirmed_by=user`、`confirmed_at` 和用户采用/修改的定义。A/B/C 文献必须为 `pdf-verified`，顺序唯一且不超过上限；D 级必须有理由。任一条件不满足时保持 `blocked_by_user` 或 `blocked_by_evidence`。

进入 S6 后按论文类型和风险动态使用 2–5 个分析视角，不再固定代理数量或统一长模板；具体规则见 `evidence-vault-playbook.md`。
