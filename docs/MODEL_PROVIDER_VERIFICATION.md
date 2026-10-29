# 模型平台切换验证记录

日期：2026-10-29

## 完成的功能

- 登录后，导航栏显示“新任务使用的模型”，列出百炼、DeepSeek 官方和 OpenAI / GPT，以及各自 `.env` 中配置的单个模型名称。选择后点击“切换”，保存为当前用户的全局偏好。
- API Key 或模型名称缺失时不可选；服务端也会校验提交。配置状态仅来自本地配置，不代表远程连接或账户模型权限已验证。页面加载与切换不会调用模型。
- 新指令和单封分析使用已选平台；指令的后续分析、重试以及新邮件/回复的对话修改沿用任务原平台。详情页展示该任务实际使用的平台和模型。
- 平台偏好不作为邮件处理偏好传给模型，模型也不能通过偏好提案修改平台。保存采用现有用户隔离、POST/CSRF 保护和偏好版本校验。
- `MAIL_ANALYSIS_EXTRA_BODY` 仅应用到 `MAIL_ANALYSIS_PROVIDER` 对应平台，其他平台使用各自的 `*_EXTRA_BODY`，避免专有参数互串。

没有新增依赖、必填环境变量、网页密钥管理、自定义接口、平台内模型选择、任务中途换平台或自动路由。真实 `.env` 保持不变；更新了 `.env.example` 的参数说明。

## 核心实现文件

| 文件 | 职责 |
|---|---|
| `email_agent/services/model_selection.py` | 用户默认平台、草稿原始平台、任务模型配置变化检查 |
| `email_agent/models/commands.py`、`services/preferences.py`、`migrations/0008_user_model_provider.py` | 复用用户偏好表保存平台，校验全局范围、已配置状态和版本 |
| `email_agent/context_processors.py`、`command_views.py`、`templates/email_agent/base.html` | 所有登录页面的选择入口与显式保存 |
| `email_agent/intelligence/models.py` | 三平台配置状态及邮件场景参数隔离；沿用已有客户端工厂 |
| `email_agent/services/commands.py`、`analysis.py`、`drafts.py` | 新任务选择与已有任务平台固定；分别接入意图解析、分析和多轮改稿 |
| `email_agent/intelligence/commands.py`、`understanding.py`、`drafting.py` | 模型调用显式接收任务平台 |

任务实际平台展示分别位于 `command_detail.html`、`analysis_detail.html`、`review_detail.html`。布局样式沿用 `static/email_agent/app.css`。执行计划和使用说明已同步更新；所有历史阶段验证报告保留。

## 针对性验证

扩展已有测试文件，新增一个平台选择用例，并扩展原有主流程的平台组合，没有增加重复测试文件。仅运行相关用例，共 **27 passed**，没有运行全项目测试。

执行命令：

```powershell
uv run pytest tests/test_commands.py tests/test_models.py tests/test_analysis.py::test_normal_analysis_page_and_duplicate_click_reuse_result tests/test_reviews.py::test_reply_conversational_revision_uses_latest_saved_draft -q --reuse-db
```

| 检查 | 结果 |
|---|---|
| 导航显示三个平台和环境配置模型，保存选择，其他用户保持独立默认值 | 通过 |
| 未配置平台、未知平台、过期偏好版本、邮箱级平台及外部跳转地址 | 拦截，未调用模型 |
| 百炼 / DeepSeek / GPT：指令解析 → 新邮件起草 → 多轮改稿 | 通过；真实 LangChain 客户端和结构化输出，HTTP 响应模拟 |
| 切换默认平台后，当前新邮件草稿继续对话 | 保持初稿平台及模型；百炼场景参数不传到其他平台 |
| 单封分析使用用户选择，重复分析复用结果 | 百炼与 DeepSeek 通过 |
| 批量计划处理一部分后切换默认平台，再继续 | 剩余邮件沿用计划平台 |
| 回复先对话、再人工编辑、再对话；用户已切换默认平台 | 回复仍沿用原分析平台，从最新草稿继续 |
| Ruff、Django check、迁移一致性 | 通过 |
| 本地迁移与服务更新 | `0008_user_model_provider` 已应用，网页镜像已更新，健康检查返回 `ready` |
| 浏览器实看导航栏 | 百炼与 DeepSeek 显示现有模型、可选；GPT 显示配置模型但因未填写密钥而禁用。默认仍为百炼，没有修改真实用户偏好 |

测试使用独立 PostgreSQL 数据库与真实 LangGraph 检查点，模型和邮箱 HTTP 均为模拟。本次未使用真实模型 API、未读取真实邮件或发送真实邮件，不能将此报告作为新增平台的远程连接验收。

## 配置和使用边界

每个平台使用已有 `DASHSCOPE_MODEL`、`DEEPSEEK_MODEL` 或 `OPENAI_MODEL` 的一个模型。修改环境变量后需重启应用；网页切换已配置平台无需重启。

尚未保存用户选择时，邮件功能使用 `MAIL_ANALYSIS_PROVIDER`，默认百炼；底层工厂直接调用的默认值仍为 `LLM_DEFAULT_PROVIDER`。禁用或删除平台偏好会恢复邮件默认值。如果已保存平台后来移除了密钥，页面显示未配置，调用失败时不会自动切换到其他平台。

环境中改变模型名称后，不会在旧任务上悄悄换模型；需要重新发起任务。已有内容仍可查看、人工修改和确认。按原有机制，未及时落库的请求重试可能产生额外用量。
