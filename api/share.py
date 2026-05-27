import json
import os
import secrets
import urllib.parse
import urllib.request
import urllib.error

from flask import Flask, jsonify, request

app = Flask(__name__)

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 헷갈리는 O/0/I/1 제외
CODE_LEN = 6


def _supabase_headers():
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _table_url():
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    return f"{base}/rest/v1/duty_rooms"


def _insert_room(code: str, member_name: str, shifts: dict) -> None:
    url = _table_url()
    payload = json.dumps({"code": code, "member_name": member_name, "shifts": shifts}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={**_supabase_headers(), "Prefer": "return=minimal"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10):
        pass


def _fetch_room(code: str) -> dict | None:
    safe_code = urllib.parse.quote(code, safe="")
    url = f"{_table_url()}?code=eq.{safe_code}&select=member_name,shifts&limit=1"
    req = urllib.request.Request(url, headers=_supabase_headers())
    with urllib.request.urlopen(req, timeout=10) as resp:
        rows = json.loads(resp.read())
    return rows[0] if rows else None


@app.route("/api/share", methods=["POST"])
def create_room():
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_KEY"):
        return jsonify({"error": "서버 설정이 완료되지 않았어요."}), 503

    body = request.get_json(force=True, silent=True) or {}
    member_name = str(body.get("member_name", ""))[:50].strip()
    shifts = body.get("shifts")

    if not member_name or not isinstance(shifts, dict):
        return jsonify({"error": "member_name과 shifts가 필요합니다."}), 400

    for _ in range(5):
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
        try:
            _insert_room(code, member_name, shifts)
            return jsonify({"code": code})
        except urllib.error.HTTPError as err:
            if err.code == 409:
                continue
            return jsonify({"error": "서버 오류가 발생했어요."}), 500
        except Exception:
            return jsonify({"error": "서버 오류가 발생했어요."}), 500

    return jsonify({"error": "코드 생성에 실패했어요. 다시 시도해 주세요."}), 500


@app.route("/api/share", methods=["GET"])
def get_room():
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_KEY"):
        return jsonify({"error": "서버 설정이 완료되지 않았어요."}), 503

    code = request.args.get("code", "").strip().upper()
    if not code:
        return jsonify({"error": "code가 필요합니다."}), 400

    try:
        row = _fetch_room(code)
    except Exception:
        return jsonify({"error": "서버 오류가 발생했어요."}), 500

    if not row:
        return jsonify({"error": "해당 코드를 찾을 수 없어요."}), 404

    return jsonify({"member_name": row["member_name"], "shifts": row["shifts"]})
