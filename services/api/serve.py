"""Run the FastAPI server: python services/api/serve.py"""
from __future__ import annotations

import uvicorn

from app.core.config import settings


def main() -> None:
    # settings.api_host 默认 127.0.0.1（API_HOST env 可覆盖）：开发入口不再
    # 默认把无 TLS 的 API 暴露到局域网；需要外部直连时显式 API_HOST=0.0.0.0。
    uvicorn.run("app.main:app", host=settings.api_host, port=settings.api_port,
                reload=False)


if __name__ == "__main__":
    main()
