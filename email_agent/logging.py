"""授权回调会携带短期授权码，访问日志同样需要隐藏这些敏感参数。"""

import logging
import re

OAUTH_QUERY = re.compile(
    r"(?i)([?&](?:code|state|session_state|access_token|refresh_token|id_token|client_secret|code_verifier)=)[^&\s\"']*"
)


class RedactOAuthQueryFilter(logging.Filter):
    def filter(self, record):
        def clean(value):
            return OAUTH_QUERY.sub(r"\1[已隐藏]", value) if isinstance(value, str) else value

        record.msg = clean(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(clean(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: clean(value) for key, value in record.args.items()}
        return True
