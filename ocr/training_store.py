"""학습 데이터 저장: 마스킹된 이미지 → Supabase Storage + OCR 라벨 → DB."""
from __future__ import annotations

import io
import json
import os
import secrets
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone

from PIL import Image


def save_sample(masked_image: Image.Image, ocr_result: dict) -> None:
    """OCR 완료 후 백그라운드 스레드에서 저장. 실패해도 OCR 결과에 영향 없음."""
    threading.Thread(
        target=_save,
        args=(masked_image, ocr_result),
        daemon=True,
    ).start()


def _supabase_headers(key: str) -> dict:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }


def _save(masked_image: Image.Image, ocr_result: dict) -> None:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return

    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        uid = secrets.token_hex(4)
        filename = f"{ts}_{uid}.jpg"

        # 1. Storage 업로드
        buf = io.BytesIO()
        masked_image.save(buf, format="JPEG", quality=85)
        img_bytes = buf.getvalue()

        req = urllib.request.Request(
            f"{url}/storage/v1/object/training-images/{filename}",
            data=img_bytes,
            headers={**_supabase_headers(key), "Content-Type": "image/jpeg"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=30)

        # 2. DB 저장 (rowIndex + shifts만 — 이름 제외)
        rows_clean = [
            {"rowIndex": r["rowIndex"], "shifts": r["shifts"]}
            for r in ocr_result.get("rows", [])
        ]
        label = {
            "year": ocr_result.get("year"),
            "month": ocr_result.get("month"),
            "rows": rows_clean,
        }
        payload = json.dumps({"image_path": filename, "ocr_result": label}).encode()
        req2 = urllib.request.Request(
            f"{url}/rest/v1/training_samples",
            data=payload,
            headers={
                **_supabase_headers(key),
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            method="POST",
        )
        urllib.request.urlopen(req2, timeout=10)

    except Exception:
        pass
