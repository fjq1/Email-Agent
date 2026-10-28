# Email Agent

基于 Django、PostgreSQL、LangChain 和 LangGraph 的邮件助理。项目当前实现阶段 0 基础设施，真实 Gmail / Outlook 接入从阶段 1 开始。

目录职责、后续功能和阶段验收见 [执行方案](docs/IMPLEMENTATION_PLAN.md)。

## 已提供的基础能力

- PostgreSQL 业务数据和 LangGraph 检查点，正式运行和测试都不使用 SQLite。
- Django 登录、CSRF 保护、用户隔离、只读运维后台。
- Celery + Redis 后台队列、定时投递、事务 Outbox 和停滞运行恢复。
- 可暂停、确认、拒绝、失败重试的基础设施诊断流程；没有邮件发送或模型费用。
- 百炼、DeepSeek 官方和 OpenAI / GPT 的统一模型工厂，平台独立配置，按需创建。
- 中文控制台、管理命令、真实 PostgreSQL 测试和 GitHub Actions 验证配置。

## 首次启动

需要 uv 和 Docker Desktop；Windows 请启用 Docker 的 Linux 容器。已有 `.env` 时，初始化脚本不会覆盖它。

```powershell
uv sync --frozen
uv run python scripts/init_env.py
docker compose --profile app up -d --build
docker compose exec web python manage.py createsuperuser
```

首次构建会下载 Python 依赖和容器镜像。`init` 服务自动运行 Django 迁移与检查点初始化，成功后才启动 Web 和 Worker。

打开 [本地控制台](http://localhost:8000/)，使用刚创建的账号登录。运维后台位于 [Django Admin](http://localhost:8000/admin/)。管理员密码通过交互输入，不会写入仓库。

```powershell
docker compose ps
docker compose exec web python manage.py check_infrastructure --worker
```

本机端口默认 PostgreSQL `55432`、Redis `56379`、Web `8000`，仅绑定本机。数据库和 Redis 使用 Docker 命名卷持久化。`docker compose stop` 停止服务时保留数据。

重新修改代码后运行 `docker compose --profile app up -d --build`。重新修改 `.env` 后也需要重新创建应用容器。PostgreSQL 首次初始化后，修改 `.env` 的数据库密码不会自动修改已有数据库用户密码，需要单独更新数据库用户。

## 在本机运行 Django

日常编辑也可以只将数据库和队列放在容器中：

```powershell
docker compose up -d postgres redis
uv run python manage.py migrate
uv run python manage.py init_checkpoints
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Celery Worker 和 Beat 运行在 Linux / WSL2 / 容器中。Windows 本机 Django 可以访问同一组 PostgreSQL 和 Redis。不要同时启动两个绑定 `8000` 的 Web 服务。

```bash
uv run celery -A config worker --loglevel=INFO --concurrency=2
uv run celery -A config beat --loglevel=INFO
```

只需要一个 Beat 调度器；同一运行的重复消息由数据库锁和幂等键处理。

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

1. 登录控制台，点击“创建诊断流程”，刷新到“等待审核”。
2. 重启 Web 和 Worker：`docker compose restart web worker`。
3. 再次打开控制台，运行记录仍在；点击“确认”或“拒绝”。
4. 刷新后状态变为“已完成”或“已拒绝”，只读后台可以看到一条诊断操作记录。

也可以在两个独立命令进程中验证检查点。将 `admin` 换成已存在的用户名，将示例 ID 换成第一条命令输出的 `run_id`：

```powershell
uv run python manage.py probe_workflow --username admin --inline
uv run python manage.py probe_workflow --username admin --run-id YOUR_RUN_ID --decision approve --inline
```

`--inline` 仅用于诊断：直接在命令进程执行图，仍使用 PostgreSQL 检查点。Web 正常路径通过 Worker 执行。重复执行恢复命令不会重复创建诊断操作。

队列离线时，运行和 Outbox 事件仍保存在 PostgreSQL。恢复队列后 Beat 会继续投递，也可以手动补偿：

```powershell
uv run python manage.py dispatch_outbox
uv run python manage.py recover_runs
```

`recover_runs` 只重新调度超过 `RUN_STALE_SECONDS` 未更新的等待执行或执行中运行；失败运行需在页面手动重试，等待审核的运行不会被自动批准。

## 测试与检查

测试会创建独立的 `test_email_agent` 数据库，需要开发用 PostgreSQL 用户拥有创建测试数据库的权限。Compose 初始化的本地用户具备此权限；部署账户应另行限制权限。测试不会调用真实模型或邮箱。

```powershell
docker compose up -d postgres redis
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run pytest
```

测试覆盖平台配置隔离、真实 PostgreSQL 检查点、重复审批、结果写入后故障重试、Outbox 投递失败、停滞运行恢复、登录和跨用户权限。持续集成使用同样的 PostgreSQL / Redis 服务。

完整启动应用容器后，可以运行真实队列与进程重启验证。该脚本创建不可登录的临时用户，成功后清理自己的诊断数据：

```powershell
uv run python scripts/verify_runtime.py --restart
```

增加 `--restart-storage` 可同时重启 PostgreSQL 和 Redis，验证持久化数据；重启只作用于此 Compose 项目，不删除数据卷。

公开探针为 `/health/live/` 和 `/health/ready/`；后者检查 PostgreSQL、Redis 与检查点表，不证明 Worker 在线。Worker 另用 `check_infrastructure --worker` 验证。

## 部署边界

当前 Compose 和 `runserver` 用于本地开发验证。生产部署需要应用服务器、HTTPS 反向代理、静态文件服务、备份与访问控制，并设置 `DJANGO_SETTINGS_MODULE=config.settings.production`、独立随机密钥与正确域名。只有可信反向代理已经正确处理协议请求头时，才启用 `DJANGO_TRUST_PROXY_SSL_HEADER`。

生产配置通过 `SYSTEM_SMTP_*` 提供 Django 系统通知后端；需要使用系统通知时再配置真实 SMTP。这不替代阶段 1 的用户邮箱 OAuth。HSTS 子域覆盖与预加载保持关闭，部署到确定域名且所有相关子域均支持 HTTPS 后，再按需启用对应的 `DJANGO_HSTS_*` 字段；因此默认部署检查会保留这两项提示。

LangGraph 表由 `init_checkpoints` 管理，业务表由 Django migrations 管理。两者的提交没有跨连接原子性保证，业务幂等记录和恢复调度处理进度偏差；此机制不能直接推导出外部邮件发送绝对只执行一次。

## 官方参考

- [OpenAI Python API](https://developers.openai.com/api/reference/python)
- [DeepSeek API](https://api-docs.deepseek.com/)
- [百炼兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)
- [LangGraph 检查点](https://docs.langchain.com/oss/python/langgraph/checkpointers)
- [LangGraph 人工中断](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [Celery 的 Windows 支持说明](https://docs.celeryq.dev/en/stable/faq.html#does-celery-support-windows)
