"""Local run: `python serve.py`. Serves the app on this computer only (http://localhost:8501; no
address for other devices on the network) with cross-origin isolation headers.

COOP/COEP make the page "cross-origin isolated", which unlocks SharedArrayBuffer so ONNX Runtime
Web can run the OCR models on several CPU cores. Streamlit Community Cloud runs app.py instead.
"""
import os
import sys

import streamlit as st
from starlette.middleware import Middleware

ISOLATION_HEADERS = [
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-embedder-policy", b"require-corp"),
    (b"cross-origin-resource-policy", b"same-origin"),
]


class CrossOriginIsolation:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                names = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    (k, v) for k, v in ISOLATION_HEADERS if k not in names
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)


app = st.App("app.py", middleware=[Middleware(CrossOriginIsolation)])


if __name__ == "__main__" and not os.environ.get("RECEIPT_SCANNER_STARTED"):
    # Start Streamlit bound to localhost; it then loads this file again to find `app`.
    os.environ["RECEIPT_SCANNER_STARTED"] = "1"
    from streamlit.web import cli

    sys.argv = ["streamlit", "run", __file__, "--server.address", "localhost", *sys.argv[1:]]
    sys.exit(cli.main())
