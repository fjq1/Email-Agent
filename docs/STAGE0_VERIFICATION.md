# 阶段 0 验证记录

验证日期：2026-10-28。

## 已执行的检查

| 项目 | 结果 |
|---|---|
| uv 依赖安装和锁定 | 完成；运行依赖与开发测试依赖分别管理 |
| Django `check` | 无错误 |
| Django 数据库迁移 | 成功应用到真实 PostgreSQL |
| 迁移一致性 | `makemigrations --check --dry-run` 无待生成迁移 |
| LangGraph 检查点初始化 | 成功，可重复执行 |
| Ruff 检查与格式化 | 通过 |
| pytest | 28 项通过，使用真实 PostgreSQL 测试数据库 |
| Docker 应用构建与启动 | PostgreSQL、Redis、Web、Worker 和 Beat 正常运行 |
| 就绪检查 | PostgreSQL、Redis、检查点表和 Worker 在线检查通过 |
| 跨进程恢复 | 真实队列暂停后重启 Web / Worker，审核后继续成功 |
| 数据持久化 | 真实队列暂停后重启 PostgreSQL / Redis / Web / Worker，记录保留并恢复成功 |
| 去重 | 重复确认和重复执行后，只有一条诊断操作结果 |
| 生产配置检查 | 无错误；默认保留 HSTS 子域覆盖、预加载两项部署提示 |
| 本地凭据隔离 | `.env` 被 Git 忽略，并排除在 Docker 构建上下文之外 |

## 自动化测试覆盖

- 三个平台独立创建模型客户端，缺失密钥和错误平台给出明确异常。
- 使用模拟 HTTP 响应验证百炼、DeepSeek 官方和 GPT 的实际请求路径、认证信息、模型名称与协议选择；没有使用真实 API 额度。
- 事务回滚时，运行、审计和 Outbox 一起回滚。
- 队列发布失败后保留事件，重新投递成功后不重复发布。
- 已过期的 Outbox 领取租约可以恢复。
- 重复审批幂等，另一用户不能审批或查看不属于自己的运行。
- 两个 PostgreSQL 连接无法同时获得同一运行的执行锁。
- PostgreSQL 检查点保存审核暂停位置，新建图与连接后仍可恢复。
- 操作结果已写入、节点随后失败时，重试复用已有结果。
- 停滞运行可以重新调度，待审核运行不会自动批准。
- 登录、POST、CSRF 保护与公开探针的信息边界。
- 审计附加字段及已知敏感值脱敏。

## 可复现命令

```powershell
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run pytest
uv run python manage.py check_infrastructure --worker
uv run python scripts/verify_runtime.py --restart-storage
uv run python manage.py check --deploy --settings=config.settings.production
```

运行真实队列验证前，请先启动完整 Compose 应用。验证脚本使用不可登录的临时用户，成功后清理该用户、诊断运行、操作、事件和检查点；不会删除数据库卷。

## 仍需用户配置的项目

三平台真实 API Key 留空；模型工厂与请求协议已通过隔离测试，真实网络、额度和账户模型权限需填写密钥后通过 `check_model` 验证。

登录账号由 `createsuperuser` 交互创建；未预置共享管理员密码。

Gmail / Outlook OAuth、真实邮件检索、邮件草稿编辑与发送属于后续阶段，本次诊断不执行邮件操作。当前容器 Web 使用开发服务器，正式部署按 README 的部署边界补齐。

GitHub Actions 配置已加入仓库；本地检查已执行，远程 CI 需后续推送后运行。
