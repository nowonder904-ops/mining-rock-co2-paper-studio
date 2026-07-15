# 审稿、修回与交付内核

## 1. 模拟审稿

模拟审稿先独立完成方法、领域、证据和反方四种检查，再综合。问题使用：

- `P0`：使核心结论、数据完整性、伦理或合规失效，阻塞推进；
- `P1`：显著削弱可信度，但可通过分析、实验、降级主张或补充说明修复；
- `P2`：表达、格式或局部可重复性问题；
- `Observation`：不阻塞的建议。

每项必须包含 `finding_id`、稿件位置、问题、影响、证据、修复动作、关闭条件。评分只能用于相对排序，不能替代具体缺陷。未关闭 P0/P1 时不得进入交付。

## 2. 真实审稿意见路由

收到决定信后先编号，编辑意见使用 `E.1`，审稿人意见使用 `R1.1`、`R2.1`。保留原评论，不改变语义。每条意见映射为一种主要动作：

```text
ACCEPT_TEXT, ACCEPT_ANALYSIS, ACCEPT_EXPERIMENT, ACCEPT_FIGURE,
CLARIFY_EXISTING, ADD_CITATION, SOFTEN_CLAIM, PARTIAL,
DISAGREE, OUT_OF_SCOPE, AUTHOR_INPUT_NEEDED, BLOCKING
```

建议维护 `10_Revision/response_tracker.csv`：

```text
comment_id,reviewer,severity,category,action,readiness,manuscript_location,evidence,author_input_needed,notes
```

`readiness` 只允许：`ready_to_submit`、`draft_with_placeholders`、`needs_author_input`、`blocked`。

## 3. 回复写法

逐条结构为：原评论 → 直接回答 → 真实动作及证据 → 稿件位置 → 剩余边界。不能声称未实际完成的实验、分析、图件、引用或修改；不能编造行号、p 值、样本量、效应量、DOI、accession 或批准号。

不同意时先承认问题的科学价值，再用本研究事实或范围理由回答。无法完成的新系统、新队列、长期试验或不同场地研究，可给出已有替代证据、降低主张并增加局限；时间、经费和方便性不能作为主要科学理由。伦理、合规、数据完整性或中心证据缺失保持 `BLOCKING`。

审计命令：

```text
python scripts/review_response_audit.py \
  --vault <论文库> \
  --source-comments <决定信或原始意见.md> \
  --tracker <response_tracker.csv> \
  --manuscript <修订稿.md>
```

所有评论有稳定 ID、回复、证据/位置或显式待办，且无占位时，才可标记 `ready_to_submit`。

## 4. 数据与材料交付检查

交付前盘点：实验原始数据、处理数据、图源数据、CT/显微/声发射等图像、样品和仪器元数据、模拟几何/网格/边界/参数/求解器/版本、校准与验证数据、敏感性和不确定性输出、现场或第三方数据、分析与绘图脚本。

每项指定公开仓库、受控访问、补充材料、第三方来源、合理申请或不适用中的一种路线。中心结论所依赖数据没有稳定访问路线时阻塞。受限矿山或储层数据须写明控制方、限制依据、申请流程及仍可公开的元数据；不得虚构标识符或许可。

## 5. 交付构建

运行：

```text
python scripts/delivery_build.py build --vault <论文库>
```

脚本使用标准库生成并校验最小有效 DOCX，同时输出 UTF-8 LaTeX。随后探测 `pandoc` 与 TeX 引擎并尝试生成 PDF。PDF 只有通过签名、文件长度和 EOF 检查才登记为有效。

若缺少适用运行时或转换失败，状态必须为 `blocked_by_runtime`。DOCX 和 LaTeX 可以作为已生成中间产物，但不得宣称交付包完成。中文稿只存在传统 PDF 引擎时按缺少 Unicode 引擎阻塞。

成功或阻塞都会生成：

- `09_Delivery/delivery_manifest.json`：源稿哈希、运行时探测、转换尝试、各产物大小/哈希/验证；
- `09_Delivery/delivery_manifest.sha256`：清单自身哈希。

重新校验：

```text
python scripts/delivery_build.py validate --vault <论文库>
```

任何产物内容改变、哈希不符、DOCX 包结构损坏、LaTeX 边界缺失或 PDF 无效均返回 `invalid`。修改稿后必须重新构建整个交付包，不手工改清单。

## 6. 冻结与发布

交付完成需要同时满足：正文完整性通过；P0/P1 关闭；题名、RQ、术语、数字、单位、图表号和引用跨文件一致；Data Availability 与真实文件/仓库一致；DOCX、LaTeX、PDF 和哈希清单有效；交付版本写入产物登记。

冻结后发生任何正文、图件、数据或引用变化，原清单状态立即失效，回到相应阶段重新验证。文件名包含“最终版”不构成冻结证据。
