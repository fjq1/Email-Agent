# Email Agent

基于 Django、PostgreSQL、LangChain 和 LangGraph 的邮件助理。当前已实现阶段 0 至 3，以及阶段 5 的指令处理与偏好管理。阶段 4 调整为后置的轻量恢复检查，具体范围见执行方案。当前保持同步运行，外部基础设施只需要 PostgreSQL。

目录职责、后续功能和阶段验收见 [执行方案](docs/IMPLEMENTATION_PLAN.md)。首次连接邮箱请按 [Gmail / Outlook 配置指南](docs/MAILBOX_SETUP.md) 创建 OAuth 应用并填写 `.env`。

## 已提供的基础能力

- PostgreSQL 业务数据和 LangGraph 检查点，正式运行和测试都不使用 SQLite。
- Django 登录、CSRF 保护、用户隔离、只读运维后台。
- 统一同步执行入口，处理结果在当前请求内产生，中断后可手动继续。
- 可暂停、确认、拒绝、失败重试的基础设施诊断流程；没有邮件发送或模型费用。
- 百炼、DeepSeek 官方和 OpenAI / GPT 的统一模型工厂，平台独立配置，按需创建。
- 中文控制台、管理命令、真实 PostgreSQL 测试和 GitHub Actions 验证配置。
- Gmail / Outlook 官方 OAuth、按用户隔离的多邮箱账户、加密授权存储和令牌刷新。
- 真实邮件搜索与分页、按需正文读取、会话分页、附件元信息和幂等缓存。
- 默认只读连接，按需单独开启发送权限；纯文本展示、一次性授权状态和受约束的分页链接。
- 优先使用百炼理解邮件：分类、带原文证据的摘要、任务候选、日期、负责人、依赖和回复草稿。
- 保存分析原文快照、歧义与推断提示；复用相同分析、保守去重、保留改期差异及来源。
- 草稿和任务编辑、版本预览、确认或不执行；结束后显式重新处理，真实 Gmail / Outlook 回复仅执行已批准的内容。
- 正式待办、前置依赖关联、状态更新；不可变审批凭据、执行结果与尝试记录。
- 自然语言指令生成可修改的检索计划，选择邮件后分批总结、提取待办或生成回复，预览后明确确认动作。
- 用户主动确认的默认时区、回复语言、风格与签名；支持全局或邮箱范围，以及修改、禁用和删除。
- 向明确地址起草新邮件；新邮件与回复都可连续对话修改，逐轮保存新版本，确认后才发送。

模型 API Key 和邮箱 OAuth 凭据分别配置。解析指令和分析邮件会调用模型，优先百炼；进入审核、编辑和确认不再次调用模型。回复或建任务可以逐项确认，也可以在展示具体内容的批量预览中选择后确认。

## 首次启动

需要 uv 和 Docker Desktop；Windows 请启用 Docker 的 Linux 容器。已有 `.env` 时，初始化脚本不会覆盖它。

```powershell
uv sync --frozen
uv run python scripts/init_env.py
docker compose --profile app up -d --build --remove-orphans
docker compose exec web python manage.py createsuperuser
```

首次构建会下载 Python 依赖和容器镜像。`init` 服务自动运行 Django 迁移与检查点初始化，成功后启动 Web。

打开 [本地控制台](http://localhost:8000/)，使用刚创建的账号登录。运维后台位于 [Django Admin](http://localhost:8000/admin/)。管理员密码通过交互输入，不会写入仓库。

主页提供“连接 Gmail / Outlook”和“连接配置”。完成 OAuth 应用注册后，可以在同一浏览器授权自己的真实邮箱。准确回调地址及权限步骤见 [邮箱配置指南](docs/MAILBOX_SETUP.md)。如果平台尚未配置，页面会展示配置提示。

```powershell
docker compose ps
docker compose exec web python manage.py check_infrastructure
```

本机端口默认 PostgreSQL `55432`、Web `8000`，仅绑定本机。数据库使用 Docker 命名卷持久化。`docker compose stop` 停止服务时保留数据。

修改代码后运行 `docker compose --profile app up -d --build --remove-orphans`。从旧版升级时，该命令也会移除旧的独立任务容器，数据库迁移会删除废弃投递表，已有用户、运行记录、审计、操作结果和检查点保留。迁移前请按自己的备份策略保存重要数据。

修改 `.env` 后也需要重新创建应用容器。PostgreSQL 首次初始化后，修改 `.env` 的数据库密码不会自动修改已有数据库用户密码，需要单独更新数据库用户。

## 在本机运行 Django

日常编辑可以只将数据库放在容器中，Django 直接在 Windows 本机运行：

```powershell
docker compose up -d postgres
uv run python manage.py migrate
uv run python manage.py init_checkpoints
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

如之前启动了容器版 Web，先运行 `docker compose stop web` 释放 `8000` 端口。当前版本无需额外启动任务进程。

## 配置三个模型平台

真实密钥填入根目录 `.env`，文件已被 Git 和 Docker 构建上下文忽略。`.env.example` 只保存字段说明。

| 平台 | 选择值 | 必填字段 | 默认接入方式 |
|---|---|---|---|
| 阿里云百炼 | `bailian` | `DASHSCOPE_API_KEY`、`DASHSCOPE_MODEL`、`DASHSCOPE_BASE_URL` | Chat Completions 兼容接口 |
| DeepSeek 官方 | `deepseek` | `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL` | `ChatDeepSeek` 专用集成 |
| OpenAI / GPT | `openai` | `OPENAI_API_KEY`、`OPENAI_MODEL`、`OPENAI_BASE_URL` | `ChatOpenAI`，默认 Responses API |

`LLM_DEFAULT_PROVIDER` 是直接调用模型工厂时的默认平台；邮件功能未设置用户选择时使用 `MAIL_ANALYSIS_PROVIDER`，默认百炼。示例模型名可修改为账户实际可用模型；基础设施不会自动下载模型，也不会自动跨平台回退。

登录后在网页顶部的“新任务使用的模型”中选择平台，点击“切换”。每个平台显示 `.env` 中的一个模型名称，不提供平台内模型选择；未填写 API Key 或模型名称的平台不可选。页面的可选状态只表示本地配置齐全，实际权限和连接需通过调用验证。选择按登录用户保存到 PostgreSQL，适用于该用户的全部邮箱，刷新或重新登录后仍保留。

指令解析、邮件分析、新邮件起草和对话改稿都使用任务绑定的平台。切换仅影响之后新建的任务；已有计划的继续处理、重试及草稿对话沿用原平台。指令详情、分析结果和草稿审核页展示本任务的平台与模型。若修改 `.env` 中某平台的模型名称，重启后需要新建相关任务，已有生成结果仍可查看及人工审核。模型密钥始终在 `.env` 中，不通过网页管理；本次没有新增依赖或必填环境变量。

实现与验证见 [模型平台切换报告](docs/MODEL_PROVIDER_VERIFICATION.md)。

百炼默认填写北京地域共享兼容地址，支持改为控制台中的业务空间专属地址；密钥必须与地域匹配。模型温度留空时不传递 `temperature`，适合不支持该参数的推理模型。平台专有参数写入对应的 `*_EXTRA_BODY` JSON 对象，具体参数以所选模型文档为准。阶段 2 使用 JSON Object 模式加 Pydantic 与原文证据校验，所选模型需支持该模式。

修改配置后重启应用，然后按需执行连接验证。以下命令会产生少量真实 API 用量：

```powershell
docker compose exec web python manage.py check_model --provider bailian
docker compose exec web python manage.py check_model --provider deepseek
docker compose exec web python manage.py check_model --provider openai
```

本机运行时将前缀换为 `uv run python manage.py`。未填写密钥时 Web 和基础诊断仍可启动；调用所选平台时才会报告缺失配置。检查成功只证明基本请求可用，不代表所有邮件理解能力已验证。

模型工厂位于 `email_agent/intelligence/models.py`：

```python
from email_agent.intelligence.models import get_chat_model

# 使用默认平台，也可以显式传入 bailian、deepseek 或 openai。
model = get_chat_model("deepseek")
```

## 连接与检验真实邮箱

Gmail 使用 `GMAIL_CLIENT_ID`、`GMAIL_CLIENT_SECRET`；Outlook 使用 `OUTLOOK_CLIENT_ID`、`OUTLOOK_CLIENT_SECRET` 和 `OUTLOOK_TENANT_ID`。OAuth 凭据从两个平台的开发者控制台创建，完整步骤见 [邮箱配置指南](docs/MAILBOX_SETUP.md)。

`MAIL_TOKEN_ENCRYPTION_KEY` 用于加密数据库中的授权数据，初始化时生成；已有授权后应保留原值。令牌刷新在当前请求内执行，不需要独立任务进程。

授权后，使用页面检索、翻页、读取正文与会话。Gmail 查询语法和 Outlook KQL 分别按原生规则处理；Graph 原生搜索最多返回 1000 条，需收窄过大的查询。正文会转为纯文本，超过长度限制会显示提示。附件只读取元信息。

可以通过命令验证某个已授权账户：

```powershell
uv run python manage.py check_mailbox --username 你的Django用户名 --account 邮箱账户UUID
```

该命令实际读取一封邮件及一页会话，并缓存到 PostgreSQL；输出不包含正文或令牌。真实 OAuth 验收记录见 [阶段 1 验证记录](docs/STAGE1_VERIFICATION.md)。

## 分析邮件（阶段 2）

1. 在已连接的邮箱中搜索并打开一封邮件，成功读取正文。
2. 在“分析这封邮件”中填写日期解释时区，例如 `Asia/Shanghai` 或 `America/New_York`，点击“分析邮件”。
3. 查看分类、摘要、原文依据、任务候选、日期及回复草稿。可以同时“需要回复”和“包含任务”。
4. 从主导航进入“候选事项”，比较一个候选关联的各封邮件、改期差异和依赖。
5. 失败或请求中断时，从已有分析进入结果页，点击“重试 / 继续分析”。

分析使用当前已缓存正文，并保存当时的不可变输入快照。需要读取原邮箱最新内容时，先刷新邮件详情。相同原文、模型、参数、时区和提示词版本会复用同一次分析；更新邮件或配置后会形成新的分析。分析成功后重新打开结果页不会再次调用模型。

新增配置（已有模型及邮箱凭据保持原样）：

```dotenv
MAIL_ANALYSIS_PROVIDER=bailian
MAIL_ANALYSIS_MAX_CHARS=24000
MAIL_ANALYSIS_EXTRA_BODY={"enable_thinking":false}
```

`MAIL_ANALYSIS_PROVIDER` 独立于模型工厂默认平台，作为用户尚未选择时的邮件功能默认值，不自动跨平台回退。`MAIL_ANALYSIS_EXTRA_BODY` 只覆盖该平台的邮件场景调用（指令解析、分析、起草和改稿）；网页切换到其他平台后不会携带这些参数，而是使用对应的 `DEEPSEEK_EXTRA_BODY` 或 `OPENAI_EXTRA_BODY`。关闭思考等选项须按模型能力调整，不支持时改为 `{}`。较长正文按 `MAIL_ANALYSIS_MAX_CHARS` 截断并明确提示；附件内容本阶段不分析。

原文证据必须逐字存在，保存字段与字符区间。截止日期同时保存原始说法、候选日期、明确时间、时区和推断标记；不会自动补成 23:59。相对日期以邮件发送时间（缺失时用接收时间）为基准；负责人或日期不明确时显示待确认。

自动去重只针对同一邮箱、同一会话、相同规范化标题、明确负责人和周期实例；没有会话标识或负责人不明时按邮件隔离。重复提醒复用候选，日期及依赖变化保留在各来源。跨会话或文字相似只提示可能重复（比较最近 100 个候选），人工合并将在审核阶段提供。周期实例不明时不跨邮件合并。

命令行与网页共用服务，UUID 从页面地址取得：

```powershell
uv run python manage.py analyze_mail --username 你的用户名 --message 邮件UUID --timezone Asia/Shanghai
uv run python manage.py analyze_mail --username 你的用户名 --analysis 分析UUID
```

模型结果一旦保存，后续候选落库或检查点失败可复用该结果。模型已响应但结果尚未来得及写入数据库时若进程退出，重试仍可能产生额外调用与费用。本阶段不宣称模型计费严格只发生一次。

阶段验证报告持续保留：[阶段 0](docs/STAGE0_VERIFICATION.md)、[阶段 1](docs/STAGE1_VERIFICATION.md)、[阶段 2](docs/STAGE2_VERIFICATION.md)、[阶段 3](docs/STAGE3_VERIFICATION.md)、[阶段 5](docs/STAGE5_VERIFICATION.md)。阶段 3 / 5 报告包含核心实现文件导航。

## 人工审核、正式待办和发送（阶段 3）

1. 在分析结果中，选择“审核并创建待办”或“进入回复审核”。首次进入只建立审核单并暂停。
2. 检查已保存的预览及来源快照。需要修改时进入独立编辑页，调整回复正文、主题、收件人、抄送，或任务标题、负责人、截止日期和依赖。
3. 保存后生成新版本，返回预览页再确认执行。发送前需处理草稿中的 `【待确认】` 占位内容。
4. “不执行此建议”结束当前版本，不发送邮件或创建待办；页面只保留“重新处理”入口。必须主动重新处理，才能创建新的待审核版本，再修改或确认。旧编辑页面也无法绕过这个结束状态。
5. 从“审核记录”查看审批历史和执行尝试；从“我的待办”查看正式任务、来源和关联依赖，并更新待办状态。

同一候选事项只有一份建任务审核单和一份正式待办，重复提醒不额外创建。用户可选择关联自己已有的前置待办，尚未关联的依赖保留为文字。已创建待办本阶段可更新状态，标题等业务字段在创建前审核中编辑。

审核页按“实际内容 → 确认操作 → 来源依据 → 历史记录”分层展示。重新处理不会调用模型或执行动作，回复会重新读取来源邮件快照；只有新版本被再次确认后才会执行。旧版拒绝、取消决定保留在数据库中，页面统一显示为“未执行”。批量计划中的旧版本仍需刷新预览后重新确认。新版样式覆盖邮箱、指令、分析、审核、待办及偏好页面，手机窄屏使用横向导航和单栏内容；无需安装前端构建工具。

此次审核交互与界面调整的核心文件、测试及视觉检查见 [界面调整验证报告](docs/UI_REVIEW_VERIFICATION.md)。历史阶段报告保持原样。

回复与编辑内容保存在本地数据库，不同步创建服务商草稿。默认回复地址取真实邮件 `Reply-To`，没有时取发件地址；审核时可以修改。当前支持单封回复及抄送，不附加文件，不自动“回复全部”。

**首次发送需升级邮箱授权**，已有只读连接无需删除：

| 平台 | 平台侧补充配置 | 本应用操作 |
|---|---|---|
| Gmail | Google Auth Platform → Data Access 增加 `https://www.googleapis.com/auth/gmail.send` | 首页或审核页点击“启用发送”，为原邮箱重新授权 |
| Outlook | Entra 应用的 Microsoft Graph **Delegated permissions** 增加 `Mail.Send`，组织策略要求时由管理员批准 | 同上，为原邮箱授权读取与发送 |

授权后从“审核记录”返回原审核单，再确认发送。只做内部待办不需要发送权限。原有应用 ID、密钥和模型配置继续使用，详见 [邮箱配置指南](docs/MAILBOX_SETUP.md)。

批准记录绑定用户、版本、内容摘要、动作内容和来源快照。发送前重新读取原邮件，内容变化会阻止发送；重新编辑后刷新来源快照，再次审核。当前核对来源邮件本身，尚不保证发现整个会话的新回复。

Gmail 使用 MIME 和回复头，Outlook 使用 Graph 原生 `reply`。页面区分执行完成、邮箱服务已受理和结果待核对，Graph `202 Accepted` 不代表送达。重复确认和已完成动作不再次执行。明确拒绝（如 HTTP 400）可手动重试；超时、服务端错误或发送中进程退出导致结果不明时，禁止重发和改版本绕过。后置的阶段 4 将补充简单人工核对入口和一次 Web 重启检查，完整故障矩阵延后评估。

## 指令处理与偏好（阶段 5）

从主导航打开“指令处理”，选择本次使用的一个已连接邮箱，例如输入：

> 总结最近7天关于项目进展的邮件，最多5封。
>
> 查找 Alice 本周发来的未读邮件，最多3封，生成简洁中文回复草稿。
>
> 记住以后默认使用简洁中文回复。

1. 点击“解析指令，查看计划”，核对处理目标、文件夹、收到日期、发件人、关键词、数量及解释时区。可以修改条件和本次回复方式，再按此范围检索。
2. 从实际结果勾选需要分析的邮件，生成摘要、待办建议或回复草稿。每次请求最多分析 2 封；较大的计划点击“继续分析”推进。
3. 检查批量预览中的实际任务或回复。需要修改时进入单项审核，保存后回到计划点击“更新批量预览中的版本”，再选择并确认。
4. 每次请求最多执行 2 项动作，剩余已确认内容通过“继续执行”推进。取消指令会停止后续批次；已经执行的动作及已有审核单保留。
5. 单项失败会保留记录，批量继续不会自动重发失败或结果未知的邮件；需要处理时进入该项审核记录。

每份计划默认最多 5 封，最高 10 封；为保持同步请求有界，检索最多扫描 30 封候选邮件。达到上限或还有下一页时提示可能存在更多结果，可收窄范围另建计划。默认检索收件箱；切换全邮箱范围时，Gmail 默认排除垃圾邮件与回收站，Outlook 可能包含已删除文件夹。排序和分词由服务商决定，当前结果不代表全部匹配。

指令中的相对日期按本次时区及当前时间解释；收到日期区间包含结束日，并按邮件元数据复核。邮件正文中的“明天”等仍按邮件时间解释。未读条件通过服务商状态复核，检索和分析不会将邮件标为已读。指令暂不执行删除、归档、日历操作或跨邮箱自动切换。

“我的偏好”提供默认时区、回复语言、回复风格和邮件签名。邮箱专属的启用偏好优先于全局偏好，禁用后回退到全局；本次临时回复要求优先于默认语言与风格。指令提出的偏好需要单独点击确认保存，不会从用户编辑中自动学习，也不能授予自动发送或自动批准权限。

新计划冻结当时的偏好，之后的修改用于新计划和新分析；已有结果不被覆盖。旧的单封邮件分析入口也会采用已确认偏好，明确输入的日期时区优先。签名由应用在生成草稿后追加，不发送给模型推断。已经存在的审核单保留此前内容及用户修改，界面会提示对照本次新分析后再决定是否编辑。

阶段 5 复用已有三平台配置，无需新增 API Key 或 Python 依赖。默认继续使用百炼；现在可以通过网页顶部切换新任务的平台，各平台模型名称仍由 `.env` 配置。

## 新邮件与多轮对话修改

用户已确认既有 Gmail / Outlook 的真实检索、审核与发送功能正常。本次在此基础上扩展起草新邮件，继续使用已有邮箱和模型配置，不需要新增授权范围、依赖或环境变量。

用户随后也已确认双平台指令新邮件的编辑、发送正常。缺少收件地址、写信内容等信息而被模型拒绝时，页面会直接展示处理结果和拒绝原因，自动取消后续处理，并提示重新下达新指令；不会显示检索条件、偏好确认或手动修改计划入口。普通模型连接/配置故障仍沿用原来的失败重试方式。验证见 [拒绝结果处理报告](docs/COMMAND_REFUSAL_VERIFICATION.md)。

1. 在“指令处理”选择发件邮箱，输入例如：`向 example@gmail.com 写一封中文邮件，主要内容是邀请对方下周讨论项目。`
2. 助理提取明确的收件人、抄送及写信要求，自动生成主题与正文；这条路径不检索来源邮件。查看计划中的草稿，打开“审核 / 对话修改草稿”。
3. 在“和助理一起修改”连续提出要求，例如“再简洁一些”“保留上一版语气，增加可选时间”“改成英文，主题和结尾也用英文”。每次生成完整的新版本，上方展示当前内容，下方保留每轮说明和草稿快照。
4. 可以穿插手动编辑；下一轮从最新保存内容继续。需要新增或更换收件人时，要求中写出完整邮箱地址，不从姓名猜测。检查收件人、主题及正文后，再点击“确认并发送”。

已有回复草稿使用同一对话入口，不必重新分析邮件。回复改稿会核对来源邮件；来源变化时保留原草稿并提示核对。已结束的建议需先“重新处理”，已发送或结果未知的动作不能直接改稿或重发。批量计划中的草稿版本被修改后，仍需“更新批量预览中的版本”并重新确认。

无论从审核记录还是指令计划确认，指令列表与详情都会按实际审批和执行结果汇总状态。单项审核发送完成后，所属计划同步完成；未知结果保留为未完成。展示时也会复核账本，避免旧汇总值造成显示不一致。相关修复见 [指令状态验证报告](docs/COMMAND_STATUS_VERIFICATION.md)。

每轮修改会调用初稿绑定的模型平台，新任务默认优先百炼，可在网页切换。模型输入包括当前完整草稿、最近 6 轮成功交流与有长度上限的版本片段，以及回复所需的有界来源文本；更早内容通过当前草稿延续，数据库保留完整轮次，页面显示最近 10 轮。重复提交同一请求标识复用已保存结果，失败保留旧草稿，刷新后可以重新提出修改要求。

称呼、正文和礼貌结束语增加了统一语言提示；未要求切换时尽量保持当前草稿语言，必要术语可保留原文。已保存签名由应用附加，要求去掉签名只影响草稿，不修改偏好。语言规则仍由模型执行，不宣称程序能识别所有混用情况。

本次不增加密送、附件或定时发送。新邮件仍保存为本地草稿，确认后才调用 Gmail 新消息发送或 Outlook `sendMail`。核心实现文件与针对性验证见 [新邮件与对话改稿报告](docs/DRAFT_CONVERSATION_VERIFICATION.md)。

## 验证暂停、重启与恢复

1. 登录控制台，展开“模型配置与基础设施诊断”，点击“创建诊断流程”，请求返回后即进入“等待审核”。
2. 重启 Web：`docker compose restart web`。
3. 再次打开控制台，运行记录仍在；点击“确认”或“拒绝”。
4. 审核请求返回后状态变为“已完成”或“已拒绝”，只读后台可以看到一条诊断操作记录。

也可以在两个独立命令进程中验证检查点。将 `admin` 换成已存在的用户名，将示例 ID 换成第一条命令输出的 `run_id`：

```powershell
uv run python manage.py probe_workflow --username admin
uv run python manage.py probe_workflow --username admin --run-id YOUR_RUN_ID --decision approve
```

Web 和命令行共用 `services/runs.py` 中的同步入口。审核暂停会将状态保存到 PostgreSQL 并结束当前请求；确认时发起新请求，从检查点继续。重复确认会复用已有操作结果。

如果请求执行途中进程退出，运行可能仍显示“等待执行”或“执行中”。可以在页面点击“继续处理”；失败运行点击“重试”。也可以使用同一运行 ID 手动继续：

```powershell
uv run python manage.py probe_workflow --username admin --run-id YOUR_RUN_ID
```

未审核的运行保持等待审核；正在被另一个请求处理的运行会返回提示。执行器通过数据库锁避免并发处理同一运行，操作幂等键避免重复写入。

当前版本在请求中等待模型和邮箱接口，适合先实现小规模闭环。长任务、定时执行和批量处理可以在后续扩展执行入口；现阶段通过页面或命令行手动触发。

## 测试与检查

测试会创建独立的 `test_email_agent` 数据库，需要开发用 PostgreSQL 用户拥有创建测试数据库的权限。Compose 初始化的本地用户具备此权限；部署账户应另行限制权限。测试不会调用真实模型或邮箱。

```powershell
docker compose up -d postgres
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run pytest
```

测试覆盖平台配置隔离、PostgreSQL 检查点、同步处理、OAuth、邮件读取和用户隔离。阶段 2 / 3 / 5 各保留一个主流程测试文件，没有重复的旧版测试或独立评估框架。阶段 5 当时新增 6 项，整体为 114 项；本次草稿扩展在原文件增加 3 项，仅运行直接相关的 12 项，结果见扩展报告，没有重跑全项目测试。自动测试的外部 HTTP 使用模拟响应，不会向真实联系人发信。持续集成只启动 PostgreSQL。

完整启动应用容器后，可以验证两个独立命令进程之间的恢复与服务重启。该脚本创建不可登录的临时用户，成功后清理自己的诊断数据：

```powershell
uv run python scripts/verify_runtime.py --restart
```

增加 `--restart-storage` 可同时重启 PostgreSQL 和 Web，验证持久化数据；重启只作用于此 Compose 项目，不删除数据卷。

公开探针为 `/health/live/` 和 `/health/ready/`；后者检查 PostgreSQL 与检查点表。详细结果通过 `check_infrastructure` 查看。

## 部署边界

当前 Compose 和 `runserver` 用于本地开发验证。生产部署需要应用服务器、HTTPS 反向代理、静态文件服务、备份与访问控制，并设置 `DJANGO_SETTINGS_MODULE=config.settings.production`、独立随机密钥与正确域名。只有可信反向代理已经正确处理协议请求头时，才启用 `DJANGO_TRUST_PROXY_SSL_HEADER`。

生产配置通过 `SYSTEM_SMTP_*` 提供 Django 系统通知后端；需要使用系统通知时再配置真实 SMTP。这不替代阶段 1 的用户邮箱 OAuth。HSTS 子域覆盖与预加载保持关闭，部署到确定域名且所有相关子域均支持 HTTPS 后，再按需启用对应的 `DJANGO_HSTS_*` 字段；因此默认部署检查会保留这两项提示。

LangGraph 表由 `init_checkpoints` 管理，业务表由 Django migrations 管理。两者的提交没有跨连接原子性保证，业务幂等记录和手动恢复处理进度偏差；此机制不能直接推导出外部邮件发送绝对只执行一次。

## 官方参考

- [OpenAI Python API](https://developers.openai.com/api/reference/python)
- [DeepSeek API](https://api-docs.deepseek.com/)
- [百炼兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)
- [LangGraph 检查点](https://docs.langchain.com/oss/python/langgraph/checkpointers)
- [LangGraph 人工中断](https://docs.langchain.com/oss/python/langgraph/interrupts)
