"""首次启动时创建本地配置；已有 .env 永远不会被覆盖。"""

import secrets
from pathlib import Path

root = Path(__file__).resolve().parent.parent
content = (root / ".env.example").read_text(encoding="utf-8")
content = content.replace(
    "replace-with-a-random-secret-at-least-50-characters-long", secrets.token_urlsafe(64)
).replace("replace-with-a-strong-local-password", secrets.token_urlsafe(24))
try:
    with (root / ".env").open("x", encoding="utf-8") as target:
        target.write(content)
except FileExistsError:
    print(".env 已存在，保留原有配置。")
else:
    print(".env 已创建，应用密钥和本地数据库密码已随机生成，请填写模型 API Key。")
