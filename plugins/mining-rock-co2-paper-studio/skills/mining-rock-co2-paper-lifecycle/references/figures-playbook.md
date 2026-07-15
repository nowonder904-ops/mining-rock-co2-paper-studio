# 独立图件工作手册

本手册适用于 S10.5。图件能力完全位于插件内部，不调用任何全局技能。数据图由确定性脚本剖析、绘制和验证；机制图只生成证据受限的详细提示词与正文占位，不在本阶段生成图片。

## 1. 数据图最小流程

### 1.1 先确认论证目标

每张图只登记一个核心主张。先明确源数据路径、字段含义、目标期刊、正文语言和图注需要披露的单位/样本量/误差/统计检验。不得从图形外观倒推不存在的结论。

### 1.2 运行时预检

```powershell
python scripts/figure_engine.py preflight
```

完整绘图需要 `matplotlib` 与 `Pillow`。缺失时脚本输出 `blocked_by_runtime` 并以退出码 3 结束；不得手工把登记表写成 `passed`。数据剖析只用标准库，即使绘图包缺失仍可运行。

### 1.3 确定性数据剖析

```powershell
python scripts/figure_engine.py profile `
  --source "<论文库>\03_Literature_or_Data\data.csv" `
  --group condition `
  --output "<论文库>\07_Manuscript\Figures\FIG-D01.profile.json"
```

只接受 UTF-8 CSV/TSV。报告包含字段类型、缺失率、组内 n、描述统计、IQR 异常、偏度、量级跨度、相关性和候选图型。Excel 应先明确导出为 CSV，避免隐藏的工作表、公式和单位问题。

### 1.4 选图硬规则

| 论证目标/数据结构 | 首选 | 拦截项 |
|---|---|---|
| 连续结果随时间/剂量变化 | line | 用柱状图隐藏趋势 |
| 两个连续变量的关系 | scatter | 把无序观测连成线 |
| 类别与连续结果 | box，叠加每个原始点 | n<10 的均值柱 |
| 单变量分布 | hist | 饼图 |
| 多连续变量相关 | heatmap | rainbow/3D 表面 |
| 类别计数 | bar | 3D 柱、饼图 |

无关双 Y 轴、分类点连线、误导性截轴、rainbow 色图、一图承载多个结论必须被劝阻。`bar` 对连续结果的组均值在任一组 n<10 时默认阻塞；只有用户明确坚持并记录理由时才传 `--allow-small-n-bar`。

### 1.5 绘图、导出与 QA

```powershell
python scripts/figure_engine.py plot `
  --source "<数据.csv>" `
  --figure-id FIG-D01 `
  --claim-id CLM-RESULT-01 `
  --claim "围压升高抑制峰后渗透率突增" `
  --target-journal "International Journal of Rock Mechanics and Mining Sciences" `
  --chart line --x confining_pressure --y permeability --group lithology `
  --output-dir "<论文库>\07_Manuscript\Figures" `
  --language zh --width 7.2 --height 4.8 --dpi 300 `
  --caption "不同岩性在各围压下的渗透率变化。" `
  --units "m²" `
  --error-definition "95% CI" `
  --statistical-test "mixed-effects model" `
  --multiple-comparison "Holm correction"
```

成功后必须同时存在：

- `FIG-D01.svg` 与 `FIG-D01.pdf`；
- `FIG-D01.png`；
- `FIG-D01.gray.png`；
- `FIG-D01.qa.json`。

QA JSON 保存源数据和产物 SHA-256、最终尺寸、DPI、字体、图注统计字段、缺字/裁切/刻度重叠与文件结构检查。任何硬检查失败时状态为 `qa_failed`。脚本输出一条可写入 `figure_register.csv` 的 `register_row`；登记后在正文加入稳定引用标记：

```markdown
<!-- DATA_FIGURE: FIG-D01 -->
```

最后必须实际读取 PNG 和灰度预览，检查图例是否压住数据、面板是否对齐、颜色在灰度下能否区分，以及视觉结论是否与登记主张一致。模型视觉复核不能替代 QA JSON，QA JSON 也不能替代视觉复核。

## 2. 图注统计字段

每个数据图 QA JSON 必须有以下非空字段：`caption`、`sample_size`、`units`、`error_definition`、`statistical_test`、`multiple_comparison`。确实不适用时写 `not_applicable`，不得留空，也不得猜测 p 值、样本量或检验方法。

## 3. 机制图提示词与稳定占位

机制图脚本不生成图片。它要求输入证据锚点、对象与尺度、空间布局、观察视角、证据支持的方向/顺序、边界条件、物理量/单位/符号、分面、样式、禁止元素和类比边界：

```powershell
python scripts/mechanism_figure.py `
  --vault "<论文库>" `
  --figure-id FIG-M01 --prompt-id PROMPT-M01 `
  --claim-id CLM-MECH-01 `
  --claim "循环注采诱发裂隙连通性演化" `
  --target-journal "目标期刊" `
  --core-mechanism "有效应力循环驱动裂隙闭合—再张开" `
  --evidence "EVID-012" --evidence "CARD-Smith-2025" `
  --object-scale "储层岩样—裂隙网络；毫米至厘米尺度" `
  --layout "左至右三阶段，基质与主裂隙分层显示" `
  --view "二维剖面示意，不使用透视三维" `
  --causal-sequence "升压→有效应力降低→裂隙张开；卸压后部分闭合" `
  --boundary-conditions "恒温、固定盐度、给定围压与孔压范围" `
  --quantities "σ3/MPa、p/MPa、k/m²；箭头方向按证据" `
  --panels "a 初始；b 注入；c 卸压" `
  --style "色盲安全蓝橙配色，7–9 pt，无装饰阴影" `
  --forbidden "无证据的矿物反应、现场尺度外推和泄漏结论" `
  --limitations "短时岩样试验不能直接证明长期场地稳定性"
```

脚本向 `Mechanism_Figure_Prompts.md` 写入带 BEGIN/END 标记的结构化记录，向 `Manuscript_Draft.md` 写入：

```markdown
<!-- FIGURE_PLACEHOLDER: FIG-M01 -->
> [机制图占位：FIG-M01；见 Mechanism_Figure_Prompts.md#prompt-m01]
```

并更新图件登记表为 `qa_status=prompt_ready`。同一 `figure_id` 或 `prompt_id` 默认拒绝覆盖；只有有意修订时使用 `--replace`。相关性、数值模拟、短时试验或跨岩性类比不得通过因果箭头升级为更强结论。

## 4. S10.5 最终质量门

```powershell
python scripts/validate_figure_stage.py --vault "<论文库>"
```

验证器不相信手工填写的 `qa_status`，而会重新检查：

1. 源 CSV/TSV 存在且哈希与 QA JSON 一致；
2. 至少一个矢量主输出、PNG、灰度预览和真实 QA JSON；
3. 产物哈希、figure ID、QA 状态和图注统计字段；
4. 数据图在正文中的引用或稳定标记；
5. figure ID、prompt ID 和 placeholder token 全局唯一；
6. 机制图提示词字段完整、无占位，并与正文稳定占位双向定位。

只有验证器输出 `status=valid` 且模型完成 PNG 视觉复核，S10.5 才能进入 S11。若论文确无数据图，登记一条 `figure_type=data, qa_status=not_applicable` 的记录并在 `notes` 写清理由；不得伪造数据图。
