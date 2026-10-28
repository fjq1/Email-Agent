# 阶段 1：真实邮箱只读接入验证记录

验证日期：2026-10-28。

## 已实现

- Gmail：官方 OAuth 授权码流程、PKCE、只读范围、稳定账户身份、刷新令牌、原生查询、分页、正文和会话读取。
- Outlook：官方 MSAL、个人及组织账户、加密令牌缓存、静默刷新、Graph 查询与原样 nextLink 分页、ImmutableId、正文和会话读取。
- 授权状态绑定 Django 用户和浏览器会话，十分钟过期，一次性消费；授权过程与持久凭据均加密保存。
- 断开连接清除本地授权，取消未完成的授权过程；迟到回调不会重新激活已断开的账户。
- 缓存按“账户 + 平台邮件 ID”去重；搜索摘要不覆盖已经读取的正文。
- HTML 转纯文本、附件仅元信息、正文与 HTTP 响应大小限制、缓存和截断提示。
- 所有读取页面按所属用户查询，私有页面禁止缓存；分页参数签名绑定账户、用户、查询和页大小。
- 回调访问日志隐藏授权码与 state；服务商错误仅转换为受控消息。
- 保留同步执行方案、原阶段 0 验收文件和仍有效的恢复诊断，不保留两套邮箱实现或临时演示数据。

## 已执行的自动化检查

| 检查 | 结果 |
|---|---|
| uv 安装与锁定依赖 | 完成 |
| PostgreSQL 迁移 | `0003_mailbox_connections` 已应用 |
| Django 系统检查 | 无错误 |
| 迁移一致性 | 无待生成迁移 |
| Ruff 检查与格式 | 通过 |
| pytest | 91 项通过，包含阶段 0 回归与阶段 1 新功能 |
| Docker 构建和启动 | Web 与 PostgreSQL 正常运行 |
| `/health/ready/` | `ready` |
| 实际访问日志脱敏 | 使用虚构回调参数验证，原值没有进入容器日志 |

测试使用真实 PostgreSQL 测试数据库及模拟的官方 API 响应，没有读取用户真实邮件或调用收费模型接口。

重点覆盖：Google 授权 URL 与 PKCE、MSAL 参数、MIME 与字符集处理、HTML 清洗、Graph 请求头与搜索语法、分页 URL 约束、OAuth 重放、会话与用户绑定、账户身份替换防护、401 单次刷新、撤销授权、网络错误、Gmail 403 配额差异、并发断开连接、缓存去重和跨用户访问。

## 真实账户验收：等待用户配置

用户尚未创建 Google Cloud / Microsoft Entra OAuth 应用。因此下列实际账户测试尚未执行，不以模拟测试替代：

- [ ] Gmail 浏览器授权、真实检索、翻页、正文和会话读取。
- [ ] Outlook 浏览器授权、真实检索、翻页、正文和会话读取。
- [ ] 真实令牌过期或服务商撤销授权后的恢复行为。

步骤见 [MAILBOX_SETUP.md](MAILBOX_SETUP.md)。根目录 `.env` 已补齐 OAuth 字段与加密密钥，用户原有的百炼、DeepSeek 模型名称和密钥保持原值。

授权完成后分别运行：

```powershell
uv run python manage.py check_mailbox --username 你的Django用户名 --account Gmail账户UUID
uv run python manage.py check_mailbox --username 你的Django用户名 --account Outlook账户UUID
```

命令通过时，表示该账户实际完成检索、正文和会话读取；连接另一个平台后单独验收。命令不输出正文或令牌，但会把读取的邮件写入该用户的本地缓存。

## 可复现检查

```powershell
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run pytest
uv run python manage.py check_infrastructure
```

本阶段只读邮箱，不包含自动分类、草稿生成或邮件发送；这些仍按执行计划在后续阶段实现。
