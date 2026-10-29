# 新邮件与多轮对话改稿验证记录

日期：2026-10-29（America/New_York）。用户已确认既有 Gmail / Outlook 的真实检索、审核、发送正常。本次在现有闭环上扩展功能，保留历史阶段及界面改进报告。

## 核心实现文件

下表列出本功能主要实现位置，不是全部修改清单。

| 文件 | 职责 |
|---|---|
| [intelligence/commands.py](../email_agent/intelligence/commands.py) | 新增 `compose_email` 意图，提取明确收件人、抄送和写信要求；缺少地址时请求补充 |
| [intelligence/drafting.py](../email_agent/intelligence/drafting.py) | 新邮件与改稿共用模型契约；语言/风格、签名、有限上下文，以及模型新增地址检查；复用百炼优先模型工厂 |
| [services/commands.py](../email_agent/services/commands.py) | 指令解析后自动起草；首次结果落库并复用，再关联待审核单，不执行邮箱搜索 |
| [services/drafts.py](../email_agent/services/drafts.py) | 新邮件审核单、对话请求去重、逐轮记录、来源及版本检查、失败保留旧稿，复用原审核版本和检查点流程 |
| [models/reviews.py](../email_agent/models/reviews.py) | 独立发件邮箱、新邮件动作、可空来源分析及 `DraftTurn` 轮次记录 |
| [services/execution.py](../email_agent/services/execution.py)、[providers/gmail.py](../email_agent/providers/gmail.py)、[providers/outlook.py](../email_agent/providers/outlook.py) | 批准后统一执行；区分新消息与回复，复用执行账本和 UNKNOWN 保护 |
| [review_views.py](../email_agent/review_views.py)、[templates/email_agent/review_detail.html](../email_agent/templates/email_agent/review_detail.html) | 对话表单、POST/用户隔离、当前草稿与每轮记录展示；新邮件无来源分析时的展示分支 |
| [tests/test_commands.py](../tests/test_commands.py)、[tests/test_reviews.py](../tests/test_reviews.py) | 两个既有文件中增加 3 项主流程：双平台新邮件及回复多轮修改，没有新建测试文件 |

数据库升级为 [0007_compose_and_conversational_drafts.py](../email_agent/migrations/0007_compose_and_conversational_drafts.py)。先从来源分析回填既有审核单的邮箱和偏好，再将发件邮箱设为非空；历史审批、执行和旧草稿保留，不伪造来源邮件。没有新增依赖或环境变量。

## 已实现的流程

1. 用户选择自己的已连接发件邮箱，输入“向 example@gmail.com 写一封主要内容为……的邮件”。
2. 模型解析意图与地址，应用编写主题、正文，保存结果和初始轮次，建立新邮件审核单；确认前没有发送请求。
3. 在审核页“和助理一起修改”逐轮提出要求，可调整主题、正文，以及本轮明确写出的收件人或抄送。每轮以当前保存草稿为准，支持穿插手动编辑。
4. 新版本待审核，旧版确认被拒绝；原有批量预览也会提示版本变化，需要刷新再确认。
5. 用户最终确认后，Gmail 发送无 `In-Reply-To` / `References` 的新消息，Outlook 调用 `/me/sendMail`；回复仍沿用原会话接口及发送前来源核对。

重复提交同一请求标识不重复解析或改稿。模型返回未明确提供的新地址、来源变化、过期版本或格式错误时，保留旧草稿并记录失败。明确发送失败仍沿用原机制；已发送、UNKNOWN 和已结束状态不能借对话绕过保护。结束且未执行的建议可以显式重新处理。

每轮保存要求、基于/结果版本、前后内容、修改说明、模型名称和用量。模型使用当前完整草稿、最近 6 轮成功交流及草稿片段（每份正文最多 2000 字符）、有界来源文本，帮助理解“上一版”等指代；页面展示最近 10 轮，其余记录仍在数据库。对话不自动保存偏好，不自动发送，也不自动切换邮箱。语言、称呼与结束语一致性已加强提示，但没有增加复杂语言检测框架。

## 针对性验证

按本次范围只运行相关用例，没有重复运行全项目测试。新增 3 项，连同本次共用服务直接涉及的既有用例，共 **12 passed**。

| 检查 | 结果 |
|---|---|
| Gmail / Outlook：写信指令 → 地址提取 → 自动草稿 → 连续两轮修改 → 确认发送最终版本 | 两个平台通过，模拟外部 HTTP；发送前没有搜索、读取或发送请求 |
| 重复指令提交、重复改稿请求和重复发送确认 | 不增加模型调用或发送次数 |
| 新增抄送地址明确来自本轮要求；模型擅自新增地址 | 正常地址进入预览，未提供地址受阻，旧稿保留 |
| 回复：对话修改 → 人工编辑 → 再对话修改 | 从最新人工版本继续，保留来源及历史，不执行发送 |
| 旧版确认、用户隔离、旧回复协议、任务审核、批量确认、未知结果和明确拒绝重试 | 本次直接相关的既有用例通过 |
| 真实百炼：合成指令解析 → 新邮件起草 → 一轮修改 | 通过，合计 3,684 tokens；无真实邮箱内容、无落库、无发送 |
| Ruff、Django check、迁移一致性 | 通过 |
| 迁移 `0007_compose_and_conversational_drafts` | 本地已应用；已有审核数据回填发件邮箱 |
| 既有数据及本地运行 | 发件邮箱缺失/来源不一致均为 0；网页镜像已更新，健康检查返回 ready |

真实模型验证使用 `me@example.com` 和 `example@gmail.com` 的合成要求，只调用模型，没有发送邮件。测试使用真实 PostgreSQL、LangGraph、LangChain 客户端和 Gmail / Graph 适配器，外部发送响应均为模拟。不能将这次模拟发送计为用户已完成真实新邮件收件验收。

## 使用与边界

- 入口：“指令处理”起草新邮件；“审核记录”里的新邮件及回复均可对话修改。完整使用说明见 [README](../README.md)。
- 沿用 `MAIL_ANALYSIS_PROVIDER=bailian`、`MAIL_ANALYSIS_EXTRA_BODY` 和已有三平台配置，不跨平台自动回退。
- 每轮修改会产生模型调用；失败或进程退出前未保存响应时，重新提出要求可能产生额外用量。
- 对话围绕同一份草稿；未实现全局聊天、自动搜索联系人、跨邮箱切换或任意工具执行。
- 收件人/抄送各最多 20 个，沿用现有地址校验；不支持密送、附件或定时发送。签名默认应用已确认内容，临时修改不会保存成偏好。
- 同步请求仍需等待本轮模型响应，没有新建后台任务或 Celery / Redis 依赖。

发送接口依据：[Gmail messages.send](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send)、[Graph sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0)。
