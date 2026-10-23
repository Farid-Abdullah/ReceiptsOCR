"""ASGI entry point: the Streamlit app plus cross-origin isolation headers.

COOP/COEP make the page "cross-origin isolated", which unlocks SharedArrayBuffer so ONNX Runtime
Web can run the OCR models on several CPU cores. Start with `streamlit run serve.py`.
"""
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
