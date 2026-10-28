# Email Agent

基于 Django、PostgreSQL、LangChain 和 LangGraph 的邮件助理。当前阶段 0 使用同步执行：Web 和命令行直接运行工作流，外部基础设施只需要 PostgreSQL。真实 Gmail / Outlook 接入从阶段 1 开始。

目录职责、后续功能和阶段验收见 [执行方案](docs/IMPLEMENTATION_PLAN.md)。

## 已提供的基础能力

- PostgreSQL 业务数据和 LangGraph 检查点，正式运行和测试都不使用 SQLite。
- Django 登录、CSRF 保护、用户隔离、只读运维后台。
- 统一同步执行入口，处理结果在当前请求内产生，中断后可手动继续。
- 可暂停、确认、拒绝、失败重试的基础设施诊断流程；没有邮件发送或模型费用。
- 百炼、DeepSeek 官方和 OpenAI / GPT 的统一模型工厂，平台独立配置，按需创建。
- 中文控制台、管理命令、真实 PostgreSQL 测试和 GitHub Actions 验证配置。

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

`LLM_DEFAULT_PROVIDER` 选择默认平台。示例模型名可修改为账户实际可用模型；基础设施不会自动下载模型，也不会自动跨平台回退。

百炼默认填写北京地域共享兼容地址，支持改为控制台中的业务空间专属地址；密钥必须与地域匹配。模型温度留空时不传递 `temperature`，适合不支持该参数的推理模型。平台专有参数写入对应的 `*_EXTRA_BODY` JSON 对象，具体参数以所选模型文档为准。兼容协议并不代表三个平台的工具调用、推理和结构化输出能力完全一致，阶段 2 将针对所选模型验收。

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

## 验证暂停、重启与恢复

1. 登录控制台，点击“创建诊断流程”，请求返回后即进入“等待审核”。
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

测试覆盖平台配置隔离、真实 PostgreSQL 检查点、请求内同步处理、重复审批、故障手动重试、中断恢复、登录和跨用户权限。持续集成只启动 PostgreSQL。

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
