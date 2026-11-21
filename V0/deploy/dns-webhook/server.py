#!/usr/bin/env python3
"""Local DNS webhook for HopWorld Router (DDNS + ACME TXT). Run on 127.0.0.1 only."""
from __future__ import annotations

import importlib.util
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent / 'backends.py'
_spec = importlib.util.spec_from_file_location('hopworld_dns_backends', _BACKEND)
_backends = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_backends)
handle_payload = _backends.handle_payload


def _check_secret(headers) -> None:
    secret = os.environ.get('HOPWORLD_DNS_WEBHOOK_SECRET', '').strip()
    if not secret:
        return
    auth = headers.get('Authorization', '')
    if auth == f'Bearer {secret}':
        return
    if headers.get('X-HopWorld-Secret', '') == secret:
        return
    raise ValueError('Webhook 鉴权失败')


class Handler(BaseHTTPRequestHandler):
    server_version = 'HopWorldDNSWebhook/1.0'

    def log_message(self, fmt, *args):
        if os.environ.get('HOPWORLD_DNS_WEBHOOK_QUIET', '').strip() not in ('1', 'true', 'yes'):
            super().log_message(fmt, *args)

    def do_POST(self):
        if self.path not in ('/', '/hook', '/dns'):
            self.send_error(404)
            return
        length = int(self.headers.get('Content-Length', '0') or 0)
        if length > 65536:
            self.send_error(413)
            return
        try:
            _check_secret(self.headers)
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode('utf-8'))
            message = handle_payload(payload)
            body = json.dumps({'ok': True, 'message': message}, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (json.JSONDecodeError, ValueError) as exc:
            body = json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False).encode('utf-8')
            self.send_response(400)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            body = json.dumps({'ok': False, 'error': str(exc)[:300]}, ensure_ascii=False).encode('utf-8')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_GET(self):
        if self.path == '/health':
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)


def main():
    host = os.environ.get('HOPWORLD_DNS_WEBHOOK_HOST', '127.0.0.1').strip() or '127.0.0.1'
    port = int(os.environ.get('HOPWORLD_DNS_WEBHOOK_PORT', '9876'))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'HopWorld DNS webhook on http://{host}:{port}/ (backend={os.environ.get("HOPWORLD_DNS_BACKEND", "cloudflare")})', flush=True)
    httpd.serve_forever()


if __name__ == '__main__':
    main()
