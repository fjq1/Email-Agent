# Gmail 与 Outlook 真实邮箱配置

邮箱接入使用 OAuth 授权码流程连接真实邮箱。模型 API Key 用于阶段 2 的邮件理解；这里的 OAuth Client ID、Client Secret 用于识别本项目在邮箱平台上的应用，两者分别配置。

## 先确认本地访问地址

默认从 **http://localhost:8000/** 打开应用并登录。注册平台回调时填写以下完整地址，包括末尾斜杠：

| 平台 | 回调地址 |
|---|---|
| Gmail | `http://localhost:8000/mailboxes/oauth/gmail/callback/` |
| Outlook | `http://localhost:8000/mailboxes/oauth/outlook/callback/` |

页面“连接配置”会展示当前 `.env` 的实际回调地址。`localhost` 和 `127.0.0.1` 的浏览器登录会话分别管理；授权开始页和回调页必须使用相同主机名。

正式部署时，将两项回调改为实际 HTTPS 域名，并在两个平台同步注册；生产配置不接受 HTTP 回调。

若嵌入式浏览器被 Google 拒绝，请在系统 Chrome / Edge 中访问上述本地地址并完成授权。无需在应用中提供邮箱密码。

## Gmail：创建 Google Cloud 应用

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)，选择或新建一个项目。
2. 在 API 库中查找 **Gmail API** 并启用。
3. 打开 **Google Auth Platform**。首次使用时先配置 **Branding**，填写应用名（例如“我的 Email Agent”）、支持邮箱和联系邮箱。
4. 在 **Audience** 中，个人 Gmail 使用 **External**，开发阶段保持 **Testing**；在测试用户列表添加实际要连接的 Gmail 地址。Workspace 组织内部应用可以按组织范围配置 Internal。
5. 在 **Data Access** 中添加本应用需要的范围：
   - `openid`
   - `https://www.googleapis.com/auth/userinfo.email`
   - `https://www.googleapis.com/auth/gmail.readonly`
6. 在 **Clients** 中创建 OAuth 客户端，应用类型选择 **Web application**。
7. 在 **Authorized redirect URIs** 中填写上面的 Gmail 回调地址。该服务端流程使用重定向 URI；无需把回调地址填到 JavaScript origins。
8. 创建后复制 **Client ID** 和 **Client Secret** 到项目根目录 `.env`：

```dotenv
GMAIL_CLIENT_ID=填写实际客户端ID
GMAIL_CLIENT_SECRET=填写实际客户端密钥
GMAIL_REDIRECT_URI=http://localhost:8000/mailboxes/oauth/gmail/callback/
```

这些值仅填写在本地 `.env`，不提交 Git。已有百炼、DeepSeek 的配置保持原样。使用现有脚本初始化项目时无需另建 Google 的 `credentials.json` 或 `token.json` 文件。

Google 官方流程参考：[Web 服务端 OAuth](https://developers.google.com/identity/protocols/oauth2/web-server)、[Gmail API 快速开始的项目与授权配置](https://developers.google.com/workspace/gmail/api/quickstart/python)。本项目是 Web 应用，使用本文的 Web client 配置。

Google 的 External / Testing 应用在申请 Gmail 范围时，刷新令牌存在 7 天过期规则；届时在本应用中重新授权。公开发布时，需要按实际用途评估 Google 的 OAuth 验证和受限范围要求。[令牌有效期说明](https://developers.google.com/identity/protocols/oauth2)、[Gmail 范围与验证说明](https://developers.google.com/workspace/gmail/api/auth/scopes)

## Outlook：创建 Microsoft Entra 应用

1. 打开 [Microsoft Entra 管理中心](https://entra.microsoft.com/)，进入 **Entra ID → App registrations → New registration**。需要具备所在租户的应用注册权限；没有该权限时请由租户管理员创建应用或分配权限。
2. 填写应用名称。若要同时连接个人 Outlook / Hotmail 和组织 Microsoft 365 邮箱，选择支持 **组织目录及个人 Microsoft 账户** 的账户类型。
3. 添加 **Web** 平台的重定向 URI，填写上面的 Outlook 回调地址。
4. 注册完成后，在 Overview 中复制 **Application (client) ID**，填入 `OUTLOOK_CLIENT_ID`。
5. 打开 **Certificates & secrets → Client secrets → New client secret**，创建客户端密码。复制新密码的 **Value（值）** 填入 `OUTLOOK_CLIENT_SECRET`；不是 Secret ID。记录到期时间，以便到期前更新。
6. 打开 **API permissions → Add a permission → Microsoft Graph → Delegated permissions**，配置：
   - `User.Read`：读取当前登录用户身份。
   - `Mail.Read`：读取当前用户的邮件正文。
7. 如果组织策略要求管理员同意，请由管理员完成相应授权。
8. 将配置写入 `.env`：

```dotenv
OUTLOOK_CLIENT_ID=填写实际应用客户端ID
OUTLOOK_CLIENT_SECRET=填写实际客户端密码的Value
OUTLOOK_TENANT_ID=common
OUTLOOK_REDIRECT_URI=http://localhost:8000/mailboxes/oauth/outlook/callback/
```

`OUTLOOK_TENANT_ID` 与应用支持的账户类型保持一致：

| 使用范围 | 配置 |
|---|---|
| 组织账户和个人账户 | `common` |
| 仅个人 Microsoft 账户 | `consumers` |
| 仅组织账户、多租户 | `organizations` |
| 指定组织、单租户 | 该组织的 Directory (tenant) ID，UUID 格式 |

MSAL 会在授权流程中处理 OpenID、离线访问、PKCE 和 nonce。读取接口使用委托权限，不需要申请全组织邮件读取权限。阶段 3 发送回复另行申请 `Mail.Send`，见下方说明。

参考：[注册 Entra 应用](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app)、[Web 回调配置](https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-redirect-uri)、[授权码流程](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)。

## 阶段 3：为已有邮箱启用发送

只做邮件理解和内部待办时保持只读即可。需要实际发送回复时：

1. **Gmail**：在 Google Auth Platform → Data Access 的原有范围中，增加 `https://www.googleapis.com/auth/gmail.send`，保存。测试模式继续保留自己的 Gmail 为测试用户。
2. **Outlook**：在原 Entra 应用 → API permissions → Microsoft Graph → **Delegated permissions** 中增加 `Mail.Send`。学校/组织策略要求时，仍需管理员批准。
3. 回到本应用首页或回复审核页，点击该邮箱的“启用发送”，选择原邮箱完成重新授权。回调地址、Client ID 与 Secret 无需改变。
4. 授权后打开“审核记录”，回到原审核单核对收件人与正文，再确认发送。

普通“连接邮箱”仍只请求只读范围；已启用发送的账户重新授权时会保留发送范围。用户拒绝发送范围时不会把只读连接误标为可发送。断开连接会清除本地发送能力标记。

本阶段使用本地草稿及确认后直接回复，无需额外申请 Gmail compose / modify 或 Graph Mail.ReadWrite。Gmail 使用 `messages.send`，Outlook 使用原生 `reply`，Graph 返回“已受理”不等于已经送达。

参考：[Gmail 发送范围](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send)、[Graph 回复权限](https://learn.microsoft.com/en-us/graph/api/message-reply?view=graph-rest-1.0)。

## 加密密钥

`MAIL_TOKEN_ENCRYPTION_KEY` 已在本机 `.env` 中生成。新项目通过 `uv run python scripts/init_env.py` 初始化时也会生成。

该密钥用于加密数据库中的刷新令牌、MSAL 缓存和临时授权流程，应与数据库备份分开保管。已有授权后不要随意替换；恢复数据库时需要对应的原密钥。模型 API Key、Django 密钥和该密钥分别管理。

## 重启并连接

容器运行方式：

```powershell
docker compose --profile app up -d --build --remove-orphans
```

本机 Django 运行方式：先完成 `uv sync --frozen` 和数据库迁移，再停止并重新启动 `uv run python manage.py runserver`。

1. 在同一个浏览器中访问 http://localhost:8000/ 并登录 Django 账号。
2. 点击“连接 Gmail”或“连接 Outlook”。
3. 在平台的官方授权页面选择邮箱并批准只读访问。
4. 返回本应用后显示已连接账户，并加载最近邮件。
5. 可以检索、进入下一页、打开正文和查看会话。

连接状态为“需要重新授权”时，点击该账户的“重新授权”，并选择同一个邮箱。连接其他邮箱使用顶部的“连接”按钮。

“断开连接”会清除本应用保存的授权凭据，并取消该平台尚未完成的本地授权过程；已经缓存的邮件保留。需要撤销服务商侧同意时，请在 Google / Microsoft 账户的应用授权管理页面操作。

## 检索与阅读

- Gmail 使用原生搜索，例如 `from:someone@example.com subject:项目 after:2026/10/01`。
- Outlook 使用 KQL，例如 `from:someone@example.com`、`subject:项目` 或关键词。两平台日期语法与搜索行为不同。Graph 原生搜索最多返回 1000 条，范围较大时请收窄条件。
- 列表每页 10 或 20 封，点击下一页继续；分页链接绑定账户、查询条件和页大小，15 分钟后需重新检索。
- 点击邮件时按需读取正文。HTML 转成纯文本展示，远程图片与脚本不加载。
- Gmail multipart 优先读取纯文本正文，必要时由 HTML 提取文字；附件只展示元信息。
- 会话页也支持分页，具体顺序按服务商返回结果展示。会话变动时，翻页结果可能变化。
- 单封正文默认最多展示 200000 字符，超出时会显示截断提示。API 响应大小也有限制，可在 `.env` 中调整。

邮箱服务同步读取邮件并缓存到本地 PostgreSQL；阶段 2 在邮件详情页提供分析与本地回复草稿，发送属于后续审核执行阶段。使用方法见根目录 README。

## 真实接入验收

授权完成后，可以通过命令行执行最小只读检查。账户 UUID 是邮件检索页面 URL 中 `/mailboxes/` 后面的 UUID：

```powershell
uv run python manage.py check_mailbox --username 你的Django用户名 --account 邮箱账户UUID
```

该命令读取一页、一封正文和一页会话，只输出读取状态与数量。它使用真实邮箱 API，并把读取结果写入该账户的本地缓存。Gmail 和 Outlook 分别运行一次即可。

## 常见问题

| 现象 | 排查方式 |
|---|---|
| 未配置 OAuth | 检查 Client ID / Secret 字段；模型 API Key 不能代替它们 |
| redirect_uri_mismatch | 平台与 `.env` 的 URI 必须逐字一致，注意主机、端口、路径及末尾斜杠 |
| 授权状态无效或已过期 | 从主页重新连接；全程使用同一浏览器、同一登录用户和同一主机名 |
| Google 测试账号被拒绝 | 确认该 Gmail 已在 Audience 的测试用户列表中，且 Gmail API 已启用 |
| Microsoft 无法注册应用或要求管理员批准 | 确认租户权限和组织同意策略，请管理员协助 |
| Microsoft 个人账号无法登录 | 应用账户类型应支持个人账户；检查 tenant 配置 |
| Microsoft 已登录但无可用邮箱 | 确认该账户实际有 Outlook / Exchange Online 邮箱和相应许可 |
| 需要重新授权 | 令牌被撤销、过期或权限不足，使用同一邮箱重新授权 |
| 403 配额或 429 限流 | 稍后重试；配额错误不会被误判为撤销授权 |
| 无法解密凭据 | 检查是否更换了加密密钥，恢复原密钥或重新连接 |

阶段 1 的自动化测试使用模拟邮箱响应；真实授权仍需要你在平台上创建应用并完成浏览器同意。没有填写 OAuth 凭据时，页面会保留配置指引。
