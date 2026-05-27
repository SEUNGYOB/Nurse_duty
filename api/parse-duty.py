import json
import queue
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import RequestEntityTooLarge

MAX_UPLOAD_MB = 10

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


def _sse(type_: str, **kwargs) -> str:
    return f"data: {json.dumps({'type': type_, **kwargs}, ensure_ascii=False)}\n\n"


@app.errorhandler(RequestEntityTooLarge)
def _too_large(_e):
    return jsonify({"error": f"파일 크기가 {MAX_UPLOAD_MB}MB를 초과했습니다."}), 413


@app.route("/api/parse-duty", methods=["POST"])
@app.route("/", methods=["POST"])
def parse_duty():
    from ocr.claude_parser import parse_duty_image_with_claude
    from ocr.duty_parser import parse_duty_image_bytes
    from ocr.training_store import save_sample as save_training_sample

    if "file" not in request.files:
        return jsonify({"error": "사진 파일을 찾지 못했어요."}), 400

    upload = request.files["file"]
    if not upload.filename:
        return jsonify({"error": "사진 파일을 찾지 못했어요."}), 400

    payload = upload.read()
    filename = upload.filename

    row_index = None
    raw_row = request.form.get("rowIndex")
    if raw_row is not None:
        try:
            row_index = max(1, min(16, int(raw_row)))
        except (TypeError, ValueError):
            pass

    year = month = None
    try:
        raw_year = request.form.get("year")
        if raw_year:
            year = max(2020, min(2099, int(raw_year)))
    except (TypeError, ValueError):
        pass
    try:
        raw_month = request.form.get("month")
        if raw_month:
            month = max(1, min(12, int(raw_month)))
    except (TypeError, ValueError):
        pass

    mode = request.form.get("mode", "claude").lower()
    if mode not in {"claude", "tesseract"}:
        mode = "claude"

    def generate():
        q: queue.Queue = queue.Queue()

        def on_progress(msg: str) -> None:
            q.put(("progress", msg))

        def run() -> None:
            try:
                if mode == "claude":
                    result = parse_duty_image_with_claude(
                        payload, filename,
                        row_index=row_index,
                        year=year, month=month,
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
