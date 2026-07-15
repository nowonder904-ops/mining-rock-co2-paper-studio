# 自动下载失败与人工补全文合同

## 触发条件

每次自动下载尝试后必须使用 `literature_register.py set-fulltext` 或等价的结构化更新写入结果，不能只在聊天中说“下载失败”。

```bash
python scripts/literature_register.py set-fulltext --vault <论文库> --paper-id <ID> --status download-failed --download-error <错误原因> --source-url <合法来源链接>
```

`fulltext_status` 为 `download-failed`、`manual-download-required`、`paywalled`、`login-required` 或 `captcha`，或 `download_error` 非空，均进入 S4.5。已登记为 `pdf-verified` 但路径为空、文件缺失或结构无效，也视为失败。

## 失败清单

运行：

```bash
python scripts/download_failure_gate.py build --vault <论文库>
```

输出：

- `03_Literature/download_failures.csv`：机器可读状态表；
- `03_Literature/download_failures.md`：向用户展示的操作清单；
- `03_Literature/Manual_Inbox/`：人工下载 PDF 的默认目录。

清单字段包括 `paper_id`、题名、DOI、失败原因、来源链接、期望文件名、实际路径、结构状态、身份核对证据和时间戳。期望文件名固定为 `<paper_id>.pdf`，避免标题非法字符和重名。

## 停止协议

只要存在未解决项：

1. 将 `pipeline_state.yaml` 的 S4.5 写为 `blocked_by_user`；
2. 展示失败清单和人工目录的绝对路径；
3. 说明用户应通过机构订阅、开放获取仓库、作者主页或其他合法渠道获取全文；
4. 结束当前推进，不轮询、不自动恢复、不进入 S5；
5. 等待用户使用任何明确、无歧义的表达通知下载完成，不要求逐字使用固定口令。

## 用户通知后的验证

收到通知后运行：

```bash
python scripts/download_failure_gate.py verify --vault <论文库>
```

第一遍验证要求：路径存在、扩展名为 `.pdf`、至少 512 字节、前五个字节为 `%PDF-`、文件尾包含 `%%EOF`，且 `paper_id` 能映射回 `literature_register.csv`。这只是结构检查，不等于内容身份正确。

结构通过后，Codex 必须读取首页或出版元数据并执行以下身份规则：

- 题名必须一致；
- DOI 在 PDF 和登记表中都可观察时必须一致；
- 任一题名或 DOI 冲突都保持未解决，不得运行 `confirm-identity`；
- PDF 未显示 DOI 时，使用题名加作者、期刊或年份交叉核对，并在证据中明确记录“DOI 未显示”及替代依据。

只有实际完成核对且没有冲突才能运行：

```bash
python scripts/download_failure_gate.py confirm-identity --vault <论文库> --paper-id <ID> --evidence <题名、DOI或替代出版信息的核对依据>
```

随后再次运行 `verify`。结构与身份均通过后才更新 `fulltext_status=pdf-verified`、`pdf_path` 和 `verified_at`。不得把 `confirm-identity` 当成绕过实际核对的按钮。

若文件使用其他名称或位置，先精确核对对应关系，再登记路径；不得靠模糊题名自动匹配多篇文件。

```bash
python scripts/download_failure_gate.py set-path --vault <论文库> --paper-id <ID> --path <PDF路径>
```

## 排除例外

只有用户明确决定不再获取某篇文献时才能排除，且必须记录用户给出的原因。系统不得替用户排除。

```bash
python scripts/download_failure_gate.py exclude --vault <论文库> --paper-id <ID> --reason <用户给出的原因>
```

排除会把 `screening_status` 设为 `exclude`。全部记录为 `verified` 或合规的 `excluded` 后，S4.5 才标为 `completed` 并推进到 S5。

## 状态、退出码与账册

- `build` 或 `verify` 输出 `blocked_by_user` 时返回退出码 `1`。这是预期的人工等待状态，不是脚本崩溃；调用方必须优先读取 JSON 的 `status`。
- 参数、CSV、状态文件或路径错误返回退出码 `2`。
- 完成返回退出码 `0`。

脚本只负责失败清单、文献登记表和 `pipeline_state.yaml`。总控在每次阻塞和恢复后还必须更新 `decision_ledger.md`，并把 `download_failures.csv`、`download_failures.md` 及验证结果登记到 `artifact_register.csv`。
