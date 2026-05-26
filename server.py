#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from werkzeug.exceptions import RequestEntityTooLarge

from ocr import parse_duty_image_bytes, parse_duty_image_with_claude, parse_duty_image_with_google

load_dotenv()

ROOT = Path(__file__).resolve().parent
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "3000"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
API_TOKEN = os.getenv("API_TOKEN", "")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")]

SERVICE_ACCOUNT_CREDENTIALS = ROOT / "credentials" / "google-service-account.json"
if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and SERVICE_ACCOUNT_CREDENTIALS.exists():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(SERVICE_ACCOUNT_CREDENTIALS)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

CORS(app, origins=ALLOWED_ORIGINS)


def _check_token() -> tuple | None:
    if not API_TOKEN:
        return None
    if request.headers.get("X-API-Token") != API_TOKEN:
        return jsonify({"error": "인증 토큰이 올바르지 않습니다."}), 401
    return None


@app.errorhandler(RequestEntityTooLarge)
def _too_large(_e: RequestEntityTooLarge):
    return jsonify({"error": f"파일 크기가 {MAX_UPLOAD_MB}MB를 초과했습니다."}), 413


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/")
@app.get("/index.html")
def index():
    return send_file(ROOT / "index.html")


@app.post("/api/parse-duty")
def parse_duty():
    err = _check_token()
    if err:
        return err

    if "file" not in request.files:
        return jsonify({"error": "사진 파일을 찾지 못했어요."}), 400

    upload = request.files["file"]
    if not upload.filename:
        return jsonify({"error": "사진 파일을 찾지 못했어요."}), 400

    payload = upload.read()

    row_index = None
    raw_row = request.form.get("rowIndex")
    if raw_row is not None:
        try:
            row_index = max(1, min(16, int(raw_row)))
        except (TypeError, ValueError):
            pass

    mode = request.form.get("mode", "claude").lower()
    if mode not in {"claude", "google", "tesseract"}:
        mode = "claude"

    try:
        if mode == "google":
            result = parse_duty_image_with_google(payload, upload.filename, row_index=row_index)
        elif mode == "claude":
            result = parse_duty_image_with_claude(payload, upload.filename, row_index=row_index)
        else:
            result = parse_duty_image_bytes(payload, upload.filename, row_index=row_index)
    except RuntimeError as error:
        return jsonify({"error": str(error), "mode": mode}), 502

    result["mode"] = mode
    return jsonify(result)


if __name__ == "__main__":
    print(f"Serving on http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, threaded=True)
