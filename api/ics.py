import base64
from flask import Flask, Response, request

app = Flask(__name__)


@app.route("/api/ics")
def serve_ics():
    data = request.args.get("d", "")
    if not data:
        return "데이터 없음", 400

    padded = data.replace("-", "+").replace("_", "/")
    padded += "=" * (4 - len(padded) % 4)

    try:
        ics_content = base64.b64decode(padded).decode("utf-8")
    except Exception:
        return "잘못된 데이터", 400

    return Response(
        ics_content,
        mimetype="text/calendar",
        headers={"Content-Disposition": "inline; filename=duty.ics"},
    )
