FROM ghcr.io/astral-sh/uv:0.13.6 AS uv
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
RUN useradd --create-home --uid 10001 app
COPY --chown=app:app . .
USER app
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
# 本镜像用于开发验证；正式部署应增加应用服务器和静态文件服务。
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000", "--noreload"]
