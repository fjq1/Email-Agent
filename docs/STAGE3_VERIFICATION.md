# 阶段 3：人工审核与执行验证记录

日期：2026-10-28（America/New_York）。阶段 0、1、2 验证报告保持原样。

## 核心实现文件

下表只列出理解本阶段功能时最值得先看的文件，不是全部修改清单。

| 主要文件 | 核心职责 |
|---|---|
| [models/reviews.py](../email_agent/models/reviews.py) | 审核单、不可变审批凭据、执行结果/尝试及正式待办的数据模型 |
| [services/reviews.py](../email_agent/services/reviews.py) | 从分析结果创建提案、编辑新版本、确认/拒绝/取消、版本校验与正式待办状态更新 |
| [workflows/review.py](../email_agent/workflows/review.py) | LangGraph 人工中断，保存进度，并在用户批准后恢复到执行节点 |
| [services/execution.py](../email_agent/services/execution.py) | 校验批准快照，事务内创建待办，发送前重新读取来源，保存发送结果并阻止不确定重发 |
| [providers/gmail.py](../email_agent/providers/gmail.py)、[providers/outlook.py](../email_agent/providers/outlook.py) | Gmail MIME 回复及 Graph 原生 reply；读取 Reply-To、按需请求发送范围 |
| [review_views.py](../email_agent/review_views.py)、[forms.py](../email_agent/forms.py) | 登录用户隔离、CSRF/POST 入口、编辑表单与日期/地址/依赖校验 |
| [templates/email_agent/review_detail.html](../email_agent/templates/email_agent/review_detail.html) | 已保存版本预览、原文证据、修改/确认/拒绝/取消入口、历史快照和执行结果 |
| [tests/test_reviews.py](../tests/test_reviews.py) | 本阶段少量主流程回归样本，邮箱 HTTP 使用模拟响应 |

数据库升级集中在 [0005_human_review.py](../email_agent/migrations/0005_human_review.py)。已有迁移保留，开发中尚未应用的临时迁移已替换为这一份最终迁移。

## 已实现

- 每个任务候选和每封邮件回复各自建立审核单，进入审核时暂停，不隐式批准。
- 编辑任务标题、说明、负责人、截止日期/时间/时区、文字依赖和已有前置待办；编辑回复的主题、正文、收件人及抄送。
- 独立编辑页保存为新版本，再回预览页确认；拒绝旧页面保存或批准新版本。
- 审批绑定用户、版本、内容摘要及完整动作快照，并保存对应来源快照。
- 确认后创建正式待办；候选与正式待办一对一，重复提醒和重复确认不会再建一份。
- “拒绝建议”和“取消本次处理”分别记录，不执行任何动作；重新编辑后可再次审核。
- 默认取实际 Reply-To（缺失时取发件地址）；用户修改后按已批准地址执行。
- Gmail 使用 `messages.send`，保留稳定 Message-ID 及适用的 In-Reply-To / References；主题匹配时关联原会话。
- Outlook 使用 Graph 原生 `reply`；`202 Accepted` 明确展示为邮箱服务已受理，不宣称送达。
- 未获本地发送授权时禁止确认；发送前核对原邮件及批准快照，来源变化时不发出发送请求。平台侧撤销授权或组织限制导致拒绝时记录失败。回复编辑保存时刷新来源快照，之后需要重新确认。
- 记录执行结果与每次尝试；明确被拒绝时可手动重试，未知结果不自动或手动重发，也不能改版本绕过。
- 正式待办列表、详情、前置关系及状态更新；运维后台只读展示审批与执行记录。
- 审核与执行不需要额外模型调用，原有百炼优先策略和配置保持不变。

## 实际验证

仅新增 `tests/test_reviews.py`，8 项主流程检查。原 Gmail 授权测试补充了按需申请发送范围的断言，没有新旧两套重复测试。

| 检查 | 结果 |
|---|---|
| 任务修改 → 新版本预览 → 旧版本确认被拒绝 → 正确版本确认 → 正式待办 | 通过 |
| 重复确认只保留一条执行和一个待办，待办状态可更新 | 通过 |
| 拒绝、取消均保留审核决定且不执行 | 通过（2 个场景） |
| Gmail / Outlook 发送已编辑的正文、收件人和抄送，重复确认不再次 POST | 通过（2 个平台） |
| 发送响应超时后 UNKNOWN，再次确认和继续处理均不重发 | 通过 |
| 明确 HTTP 400 拒绝后手动重试同一批准版本 | 通过 |
| 未授予发送权限不可批准发送，其他用户不可读取/编辑/批准 | 通过 |
| 邮件 Date 使用 +08:00、数据库保存 UTC 的正常路径 | 已包含在双平台发送用例中，通过 |
| 全部既有与新增测试 | **108 passed** |
| Ruff、Django check、迁移一致性 | 通过 |
| 本地迁移 `0005_human_review` | 已应用 |
| 本地 Docker Web / PostgreSQL、容器内检查及 `/health/ready/` | 正常，服务返回 ready |

自动化检查使用真实 PostgreSQL、LangGraph 检查点、HTTP 适配器及 MIME 编码；模拟邮箱 HTTP 和令牌刷新，不使用用户真实授权执行发送。本阶段没有向真实联系人发信，也没有额外调用真实模型。

## 使用方式

打开现有分析结果，进入某个任务或回复的审核单。先查看来源证据及快照，修改需要调整的字段并保存，再确认当前版本。

回复发送需要按 [MAILBOX_SETUP.md](MAILBOX_SETUP.md) 为原应用补充 Gmail `gmail.send` 或 Graph 委托 `Mail.Send`，然后在首页或审核页点击“启用发送”。已有 OAuth 应用凭据不变，已有只读连接、邮件和分析保留。授权完成后从“审核记录”返回原审核单再确认。

真实邮箱的发送授权、发信、已发送文件夹核对及收件端送达，需由用户选择具体邮件并明确确认后验收。本次报告不将模拟协议检查视为真实发信成功。

## 当前边界与下一阶段

- 草稿在本地保存，未创建服务商草稿；当前不提供附件发送、自动回复全部、撤回邮件或外部任务系统写入。
- 已创建待办可更新状态；业务字段在创建前审核中编辑，后续可扩展正式待办的改期和版本更新。
- 自动去重沿用阶段 2 的保守候选规则，跨会话或语义相似仅提示。
- 当前核对来源邮件本身，未遍历整个会话判断是否出现新回复。
- 发送已获服务商受理不代表邮件已送达。网络超时、5xx 或请求中断后结果不确定时，保留 UNKNOWN 并停止重发。
- 阶段 4 将补齐未知结果核对入口，以及发送前后断网、进程/数据库故障、部分成功等完整验证。本阶段只做必要的重复执行保护和少量恢复主流程检查。

接口依据：[Gmail messages.send](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send)、[Gmail 会话关联规则](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages)、[Graph reply](https://learn.microsoft.com/en-us/graph/api/message-reply?view=graph-rest-1.0)。
