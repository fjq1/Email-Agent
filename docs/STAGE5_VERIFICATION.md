# 阶段 5：指令处理与已确认偏好验证记录

日期：2026-10-29（America/New_York）。阶段 0 至 3 验证报告保留原样。阶段 4 本次仅修改执行计划，暂后置，不宣称完成人工核对入口或故障验证。

## 核心实现文件

下表用于定位本阶段的主要实现，不是全部修改文件清单。

| 主要文件 | 核心职责 |
|---|---|
| [models/commands.py](../email_agent/models/commands.py) | 指令计划、逐邮件进度、具体动作版本及明确确认的偏好；数据库唯一约束保证计划内去重 |
| [intelligence/commands.py](../email_agent/intelligence/commands.py) | 将自然语言转成有限的结构化计划，校验范围、数量与偏好原文证据；不向模型提供执行工具 |
| [services/commands.py](../email_agent/services/commands.py) | 检索、选择、逐批分析、冻结批量确认清单、继续/取消及错误进度；复用阶段 2 / 3 服务 |
| [services/search.py](../email_agent/services/search.py)、[providers/outlook.py](../email_agent/providers/outlook.py) | 转换 Gmail / Graph 检索语法，按时区复核收到日期与已读状态；Graph 文件夹范围及对应分页路径校验 |
| [services/preferences.py](../email_agent/services/preferences.py) | 偏好值校验、全局/邮箱范围覆盖、显式保存、版本控制、启停和删除 |
| [services/analysis.py](../email_agent/services/analysis.py)、[intelligence/understanding.py](../email_agent/intelligence/understanding.py) | 在单封及批量分析中冻结偏好与临时要求，应用语言/风格，生成后追加用户签名 |
| [command_forms.py](../email_agent/command_forms.py)、[command_views.py](../email_agent/command_views.py) | 当前用户邮箱选择、可编辑条件、POST 操作、偏好管理与权限边界 |
| [templates/email_agent/command_detail.html](../email_agent/templates/email_agent/command_detail.html) | 范围、实际邮件、分析进度、完整动作内容、更新预览、明确确认和继续处理界面 |
| [services/reviews.py](../email_agent/services/reviews.py) | 抽取共用的批准前校验，单项和批量复用同一内容、版本、发送权限与占位内容检查 |
| [tests/test_commands.py](../tests/test_commands.py) | 本阶段 6 项主流程检查，使用真实 PostgreSQL、模型客户端和邮箱适配器，模拟外部 HTTP |

数据库升级集中在 [0006_commands_preferences.py](../email_agent/migrations/0006_commands_preferences.py)，包含指令/偏好表及邮件已读缓存字段。本阶段没有新增 Python 依赖，模型工厂继续支持百炼、DeepSeek 和 OpenAI。

## 已实现

- 指令入口明确选择一个自己的已连接邮箱，先解析并展示计划，不直接检索、发送或创建正式任务。
- 支持检索、摘要、提取待办和生成回复草稿；范围包括收件箱/已发送/服务商全邮箱、发件人、收件人、主题、关键词、收到日期与已读状态。
- 计划可在分析前编辑；修改会清空旧检索选择，避免旧结果与新条件混用。指令提交标识防止重复点击产生重复计划和模型调用。
- 默认最多 5 封，每计划最多 10 封；每次检索最多扫描 30 封候选邮件，存在更多候选时明确提示。每次请求最多分析 2 封或执行 2 项动作，其余由用户点击继续。
- 选择邮件后保存进度和当时偏好；分析复用阶段 2 的原文证据、日期解释、保守去重和 LangGraph 工作流。
- 批量预览展示实际保存的任务或回复内容，支持进入原有单项编辑页；修改后需更新预览版本再确认。批量确认冻结所选动作版本和内容摘要。
- 执行复用阶段 3 的不可变审批和执行账本；确认前检查所有所选项目，逐项记录结果。批量继续不会自动重试失败或 UNKNOWN 邮件。
- 已有提案保留此前用户修改及审批状态，界面提示与本次分析比较；不自动覆盖或重复加入同一计划。
- 可取消后续批次；既有分析、审核单和已经执行的动作保留。取消不回滚已发邮件或已建待办。
- 支持默认时区、回复语言、回复风格和邮件签名；全局与邮箱范围分别管理，邮箱启用偏好优先，本次临时语言/风格优先于默认。
- 偏好需要明确保存，可修改、禁用或删除；指令提出的每项偏好单独确认，不从编辑行为自动学习，不允许偏好自动批准发送。
- 单封邮件分析入口也采用已确认偏好，显式时区输入优先。签名由应用在生成草稿后追加，不作为模型推断内容。
- 界面说明检索上限、范围差异、失败处理和已冻结偏好。主要业务代码添加中文注释。

## 实际验证

新增一个 `tests/test_commands.py`，共 6 项主流程检查；调整原有共享 HTTP 模拟 fixture，保留真实 SDK 对客户端类型的检查，没有另建重复测试工具或故障测试平台。

| 检查 | 结果 |
|---|---|
| 偏好明确保存 → 邮箱覆盖全局 → 修改 → 禁用后回退 → 删除 | 通过 |
| 指令预览不访问邮箱、重复提交不重复解析；双平台按明确范围检索 | 通过（Gmail / Outlook 两项） |
| 3 封邮件分两次分析，应用风格/签名，批量确认前不创建正式待办，2+1 执行及重复继续不重复建任务 | 通过 |
| 单项内容被编辑后，整个批量确认在写入前受阻；更新预览后使用新版本 | 通过 |
| 指令偏好不自动保存，独立确认后生效；其他用户无法查看计划、确认或访问偏好 | 通过 |
| 阶段 0 至今全部测试 | **114 passed**，本阶段新增 **6** 项 |
| Ruff 静态检查及格式检查 | 通过 |
| Django check、迁移一致性检查 | 通过；无缺失迁移 |
| 本地业务数据库迁移 `0006_commands_preferences` | 已应用，已有用户、邮箱、分析及审核数据保留 |
| 真实百炼指令解析 | 通过；合成示例解析出回复草稿、3 封上限、日期范围和两项待确认偏好；未保存偏好或执行动作 |
| 真实 Gmail / Outlook 新检索表达式及收件箱范围 | 均通过；使用无匹配的合成主题和日期区间，各返回 0 封、无下一页 |
| 本地 Docker Web / PostgreSQL、容器内基础设施检查及 `/health/ready/` | 均正常；新网页已构建启动，探针返回 ready |

真实百炼解析使用现有 `MAIL_ANALYSIS_PROVIDER=bailian` 及场景参数，单次记录 1,634 tokens。输入为合成指令与示例邮箱元信息，没有发送真实邮件正文。真实邮箱检查只读并只输出状态和结果数量，不发送邮件，不保存任何偏好。

本阶段自动测试的模型/邮箱 HTTP 均为模拟响应；沿用实际 LangChain 客户端、Gmail / Graph 适配器、PostgreSQL 和逐项审核图。真实邮箱发信、收件端送达和完整故障恢复不计入本次验收。

## 使用方式

1. 从主导航进入“指令处理”，选择邮箱，输入摘要、待办或草稿指令。
2. 核对计划，必要时修改条件；按此范围检索后选择邮件分析，大于 2 封时点击继续。
3. 查看分析证据与批量动作内容，需要修改则进入单项审核保存，并回计划更新预览。
4. 勾选并确认具体动作。发送仍需已授予相应 Gmail / Outlook 发送权限；剩余批次点击继续，失败项进入单项记录。
5. 在“我的偏好”设置全局或邮箱默认值；指令提出的偏好需要单独点击确认保存。

完整命令、上限和配置见 [README](../README.md)。历史报告保持原样，阶段 3 对阶段 4 的旧展望以更新后的 [执行计划](IMPLEMENTATION_PLAN.md) 为准。

## 当前边界

- 只支持一个明确选择的邮箱，不执行删除、归档、标记已读、日历或任意外部工具调用。
- 收到日期按计划时区过滤；正文中的相对截止日期仍按邮件时间解释。Gmail 使用时间戳边界，Graph 先扩大日期召回范围，再按实际元数据复核。
- 检索有扫描上限，服务商索引、排序和分词各有差异；全邮箱覆盖也可能不同，结果不承诺全量。
- 每份计划只保存一份明确的批量决定，未选审核单仍可单独处理。更多邮件或不同范围需新建计划。
- 偏好和分析请求参与快照与复用判定；修改偏好只影响新计划和新分析，不修改旧批准内容。
- 同步请求仍需等待本批模型及邮箱响应；没有自动后台续跑、定时任务或新增 Celery / Redis 依赖。
- 保留阶段 3 的重复执行和 UNKNOWN 停发保护。阶段 4 的简单人工核对入口尚未实现；进程/数据库故障矩阵暂缓。

检索设计参考：[Gmail 过滤与时区](https://developers.google.com/workspace/gmail/api/guides/filtering)、[Microsoft Graph 邮件搜索](https://learn.microsoft.com/en-us/graph/search-query-parameter)、[Graph 文件夹邮件路径](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0)。
