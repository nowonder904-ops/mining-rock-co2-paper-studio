# 独立运行边界

## “独立”的精确定义

本插件不读取、不探测、不安装、不调用其他全局 SKILL。卸载旧技能后，阶段路由、模板、状态、人工闸门和确定性检查仍由插件自身完成。

独立不等于凭空拥有外部资源。下列能力仍受本机和服务状态约束：网络、数据库/API、出版商权限、浏览器登录、OCR、PDF 解析器、Python 绘图库、字体、Pandoc/TeX、Obsidian 应用。任何缺失都必须结构化报告，不能由模型自述“已完成”替代。

## 能力与降级

| 能力 | 核心条件 | 缺失时行为 |
|---|---|---|
| 状态、CSV、Markdown、Base、Canvas、DOCX、LaTeX 源 | Python 3.10+ 标准库 | 核心流程阻塞 |
| 学术数据库检索与 OA 下载 | 网络及目标服务可用 | 标记 external_unavailable，保留已取结果；不得声称完整覆盖 |
| PDF 结构/身份核验 | 标准库 + 模型读取首页 | 结构可查但身份未核对时保持 S4.5 |
| PDF 文本提取 | pypdf 或 PyMuPDF，扫描件另需 OCR | blocked_by_runtime；不得用摘要冒充 |
| 投稿级数据图 | matplotlib + numpy；扩展 EDA 可用 pandas；PNG QA 可用 Pillow | S10.5 blocked_by_runtime；仍可登记需求和机制图提示词 |
| Word | 插件标准库 DOCX 生成器 | 正常生成并做 ZIP/XML 检查 |
| PDF | Pandoc + XeLaTeX/PDFLaTeX，或 Windows Word 的显式 COM 导出授权 | 生成 LaTeX/Word，但 PDF 状态 blocked_by_runtime |
| Obsidian 渲染/CLI | Obsidian 可选 | 直接文件操作和离线验证继续；只缺应用烟雾测试 |

运行 `python scripts/runtime_preflight.py` 获取本机报告。脚本不得自动安装包或程序；若用户要求补齐运行环境，再单独说明来源、权限、影响和重启要求。

## 外部事实

影响因子、分区、作者指南、数据共享政策、出版费用、数据库接口和模型/软件版本都可能变化，使用时实时核验。没有联网或用户材料时，保留显式占位，不把旧参考资料当作当前事实。

## 完整功能声明

只有对应运行条件满足且专用检查器通过，才可说该能力完成。例如有 SVG 但无 PNG/QA 不等于投稿级图；有 DOCX 但无 PDF 工具不等于完整投稿包；有摘要但无 PDF 不等于精读完成。
