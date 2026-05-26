import sys
from pathlib import Path

# Make the project root importable so `ocr/` package is found.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

MAX_UPLOAD_MB = 10

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


@app.errorhandler(RequestEntityTooLarge)
def _too_large(_e):
    return jsonify({"error": f"파일 크기가 {MAX_UPLOAD_MB}MB를 초과했습니다."}), 413


@app.route("/api/parse-duty", methods=["POST"])
def parse_duty():
    # Lazy imports so the module loads fast on cold start.
    from ocr.claude_parser import parse_duty_image_with_claude
    from ocr.duty_parser import parse_duty_image_bytes

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

    year = None
    month = None
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

    try:
        if mode == "claude":
            result = parse_duty_image_with_claude(
                payload, upload.filename,
                row_index=row_index,
                year=year, month=month,
            )
        else:
            result = parse_duty_image_bytes(payload, upload.filename, row_index=row_index)
    except RuntimeError as error:
        return jsonify({"error": str(error), "mode": mode}), 502

    result["mode"] = mode
    return jsonify(result)
