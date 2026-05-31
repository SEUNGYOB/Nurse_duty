import json
import os
import time
import urllib.request

_WINDOW = 60  # seconds


def _get_ip(request) -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    return xff.split(",")[0].strip() if xff else (request.remote_addr or "unknown")


def check(request, endpoint: str, limit: int) -> bool:
    """Return True if allowed, False if rate-limited. Fails open on error."""
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if not key or not base:
        return True

    ip = _get_ip(request)
    window = int(time.time()) // _WINDOW
    row_id = f"{ip}:{endpoint}:{window}"

    url = f"{base}/rest/v1/rpc/increment_rate_limit"
    payload = json.dumps({"p_id": row_id}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            count = int(json.loads(resp.read()))
        return count <= limit
    except Exception:
        return True  # fail open — don't block users if Supabase is down
