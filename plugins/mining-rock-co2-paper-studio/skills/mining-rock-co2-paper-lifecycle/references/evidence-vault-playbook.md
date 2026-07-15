# Mode B 精读与证据库工作手册（S5.5–S7）

## 前置条件

仅处理用户已确认的 A/B/C 文献；每篇必须有稳定 `paper_id`、`pdf-verified` 全文、Mode B 等级、顺序、批次和图表要求。D 级保留理由，不进入精读。

先运行：

```bash
python scripts/evidence_vault.py prepare --vault <论文库>
python scripts/pdf_text_extract.py --pdf <已核验PDF> --output <临时JSON>
```

文本提取只是辅助，不能代替查看原 PDF、首页身份核验或图表阅读。扫描 PDF 无可用文本时，报告 OCR/视觉运行时缺口，不根据摘要补写全文结论。

## 动态精读深度

不要固定每篇 6–8 个代理或统一长模板。根据等级、论文类型和风险选择 2–5 个独立视角，再综合冲突：

- A：全文、全部关键图表/公式、方法复现、结果统计、局限与反证；
- B：完整正文和与核心主张相关的图表/公式；
- C：研究设计、可引用背景/参数范围、关键限制；
- 实验论文重点看样品/岩性、含水与各向异性、应力路径、温压/盐度/气体相态、重复与误差；
- 数值论文重点看控制方程、参数来源、网格/时间步、边界条件、标定、独立验证和敏感性；
- 现场论文重点看场地代表性、监测时空分辨率、反演假设、缺测与替代解释；
- 理论论文重点看假设、推导条件、量纲、极限情况和可检验预测。

## 单篇精读记录

模型基于全文生成 JSON 记录，再交给 `evidence_vault.py ingest`。每条证据至少包含：

`evidence_id`、`claim_id`、主张、主张类型、原文定位、证据类型、对象、尺度、条件、方向、效应量/范围、支撑等级、限制、反证和图表/公式 ID。

主张类型区分 experiment、numerical-simulation、theory、field-monitoring、statistical-association、literature-synthesis、author-inference。支撑等级只用 strong、partial、background、limiting、metadata-only。除 metadata-only 外，`source_locator` 必须含 PDF 页码、章节、图、表或公式位置。

不确定、冲突或全文未提供的信息写成 gap，不能用领域常识填入原论文结果。公式需解释符号、量纲、假设和物理意义；数值结果不得升级为现场证据，短时试验不得升级为长期封存安全。

## 回写

```bash
python scripts/evidence_vault.py ingest --vault <论文库> --paper-id <ID> --record <记录JSON>
python scripts/evidence_vault.py validate --vault <论文库>
```

一次 ingest 原子更新：阅读笔记、逐篇引用卡、引用卡索引、Evidence Ledger、Claim–Citation Map、主题 Wiki、机制—证据 Canvas 和文献登记表。自动区块用标记增量更新；标记外的用户笔记和手工链接必须保留。

## S7 文献综述

只从已验证证据构建：时间/方法脉络、对象—条件—尺度矩阵、相互支持与冲突、参数范围、方法缺口、证据缺口和工程迁移边界。研究空白必须能映射到已检索范围和具体来源；“很少研究”“尚无研究”若没有可复现检索证据，只能写成待核验假设。

综述正文按问题组织，不按文献逐篇罗列。任何机制综合都同时列出适用条件、反例和尚不能声称的内容。
