import base64
from flask import Flask, Response, request

app = Flask(__name__)


# 한 달치 듀티 ICS는 base64로도 수만 자 수준 — 그 이상은 남용으로 간주
MAX_DATA_LEN = 100_000


@app.route("/api/ics")
def serve_ics():
    data = request.args.get("d", "")
    if not data:
        return "데이터 없음", 400
    if len(data) > MAX_DATA_LEN:
        return "데이터가 너무 큽니다", 413

    padded = data.replace("-", "+").replace("_", "/")
    padded += "=" * (4 - len(padded) % 4)

    try:
        ics_content = base64.b64decode(padded).decode("utf-8")
    except Exception:
        return "잘못된 데이터", 400

    if not ics_content.lstrip().startswith("BEGIN:VCALENDAR"):
        return "잘못된 데이터", 400

    return Response(
        ics_content,
        mimetype="text/calendar",
        headers={"Content-Disposition": "inline; filename=duty.ics"},
    )
