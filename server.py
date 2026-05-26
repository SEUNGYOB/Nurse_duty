#!/usr/bin/env python3
from __future__ import annotations

import cgi
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ocr import parse_duty_image_bytes


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 3000


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self.serve_file(ROOT / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/README.md":
            self.serve_file(ROOT / "README.md", "text/markdown; charset=utf-8")
            return

        file_path = ROOT / parsed.path.lstrip("/")
        if file_path.exists() and file_path.is_file():
            content_type = "image/jpeg" if file_path.suffix.lower() in {".jpg", ".jpeg"} else "application/octet-stream"
            self.serve_file(file_path, content_type)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/parse-duty":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            },
        )
        upload = form["file"] if "file" in form else None
        if upload is None or not getattr(upload, "file", None):
            self.send_json({"error": "사진 파일을 찾지 못했어요."}, status=HTTPStatus.BAD_REQUEST)
            return

        payload = upload.file.read()
        row_index = None
        if "rowIndex" in form:
            try:
                row_index = max(1, min(16, int(form.getfirst("rowIndex"))))
            except (TypeError, ValueError):
                row_index = None

        result = parse_duty_image_bytes(payload, upload.filename or "", row_index=row_index)
        self.send_json(result)

    def serve_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Serving on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
