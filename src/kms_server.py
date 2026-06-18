"""Compatibility entrypoint for the standalone KMS service."""

from __future__ import annotations

import os

from src.kms.server import app


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("KMS_PORT", "8421"))
    uvicorn.run("src.kms.server:app", host="127.0.0.1", port=port, reload=False)
