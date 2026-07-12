#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import queue
import threading
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_file, send_from_directory, stream_with_context
from flask_cors import CORS
from werkzeug.exceptions import RequestEntityTooLarge

from ocr import parse_duty_image_bytes, parse_duty_image_with_claude, parse_duty_image_with_google
from ocr.request_params import parse_ocr_request_context
from ocr.training_store import save_sample as save_training_sample

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


def _sse(type_: str, **kwargs) -> str:
    return f"data: {json.dumps({'type': type_, **kwargs}, ensure_ascii=False)}\n\n"

@app.errorhandler(RequestEntityTooLarge)
def _too_large(_e: RequestEntityTooLarge):
    return jsonify({"error": f"파일 크기가 {MAX_UPLOAD_MB}MB를 초과했습니다."}), 413


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/ics")
def serve_ics():
    import base64
    data = request.args.get("d", "")
    if not data:
        return jsonify({"error": "데이터 없음"}), 400
    if len(data) > 100_000:  # 한 달치 ICS는 base64로도 수만 자 수준
        return jsonify({"error": "데이터가 너무 큽니다"}), 413
    padded = data.replace("-", "+").replace("_", "/")
    padded += "=" * (4 - len(padded) % 4)
    try:
        ics_content = base64.b64decode(padded).decode("utf-8")
    except Exception:
        return jsonify({"error": "잘못된 데이터"}), 400
    if not ics_content.lstrip().startswith("BEGIN:VCALENDAR"):
        return jsonify({"error": "잘못된 데이터"}), 400
    return Response(
        ics_content,
        mimetype="text/calendar",
        headers={"Content-Disposition": "attachment; filename=duty.ics"},
    )


@app.get("/")
@app.get("/index.html")
def index():
    response = send_file(ROOT / "index.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/<path:filename>")
def serve_static(filename):
    file_path = ROOT / filename
    if file_path.exists() and file_path.is_file():
        response = send_from_directory(ROOT, filename)
        # HTML 파일: no-cache 헤더
        if filename.endswith(".html"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response
    return jsonify({"error": "파일을 찾을 수 없습니다."}), 404


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
    filename = upload.filename

    ocr_context = parse_ocr_request_context(request.form)
    row_index = ocr_context["row_index"]
    year = ocr_context["year"]
    month = ocr_context["month"]

    mode = request.form.get("mode", "claude").lower()
    if mode not in {"claude", "google", "tesseract"}:
        mode = "claude"

    refine_row_indices = None
    raw_refine = request.form.get("claudeRefineRows")
    if raw_refine:
        parsed = []
        for token in raw_refine.split(","):
            try:
                parsed.append(max(1, min(16, int(token.strip()))))
            except (TypeError, ValueError):
                pass
        if parsed:
            refine_row_indices = sorted(set(parsed))
    shift_aliases = ocr_context["shift_aliases"]

    def generate():
        q: queue.Queue = queue.Queue()

        def on_progress(msg: str) -> None:
            q.put(("progress", msg))

        def run() -> None:
            try:
                if mode == "google":
                    result = parse_duty_image_with_google(payload, filename, row_index=row_index)
                elif mode == "claude":
                    result = parse_duty_image_with_claude(
                        payload, filename,
                        row_index=row_index,
                        year=year, month=month,
                        shift_aliases=shift_aliases,
                        refine_row_indices=refine_row_indices,
                        on_progress=on_progress,
                        on_training_data=save_training_sample,
                    )
                else:
                    result = parse_duty_image_bytes(payload, filename, row_index=row_index)
                result["mode"] = mode
                q.put(("result", result))
            except Exception as exc:
                q.put(("error", str(exc)))

        threading.Thread(target=run, daemon=True).start()

        while True:
            type_, data = q.get()
            if type_ == "progress":
                yield _sse("progress", message=data)
            elif type_ == "result":
                yield _sse("result", data=data)
                break
            else:
                yield _sse("error", message=data)
                break

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    print(f"Serving on http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, threaded=True)
