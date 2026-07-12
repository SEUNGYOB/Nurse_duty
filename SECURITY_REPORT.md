# 보안 취약점 보고서

**작성일:** 2026-05-31  
**대상:** Nurse Shift Board (project-cvl1r.vercel.app)  
**범위:** `api/`, `index.html`, `vercel.json`, `sw.js`

---

## 요약

| 심각도 | 건수 | 수정 완료 | 잔여 |
|--------|------|-----------|------|
| 🔴 Critical | 1 | 1 | 0 |
| 🟠 High | 2 | 2 | 0 |
| 🟡 Medium | 3 | 3 | 0 |
| 🔵 Low | 2 | 0 | 2 |
| ✅ 양호 | 6 | — | — |

---

## 취약점 상세

### 🔴 C-01 — API 비용 남용 (rate limiting 없음)
**파일:** `api/parse-duty.py`  
**상태:** ✅ 수정 완료 (커밋 5712fcc)

**내용:** `/api/parse-duty` 엔드포인트에 인증 및 횟수 제한 없음. URL 알면 누구나 Claude Vision API 무제한 호출 가능 → API 비용 폭탄.

**수정:** `api/_rate_limit.py` 생성. Supabase `rate_limits` 테이블 + `increment_rate_limit` RPC 사용. IP당 분당 5회 제한.

---

### 🟠 H-01 — 파일 타입 미검증
**파일:** `api/parse-duty.py`  
**상태:** ✅ 수정 완료 (커밋 5712fcc)

**내용:** 어떤 파일이든 Claude API로 전송됨. ZIP, PDF, 악성 파일도 통과.

**수정:**
1. MIME 타입 화이트리스트 (`image/jpeg`, `image/png`, `image/webp`, `image/heic`, `image/heif`)
2. 매직 바이트 검사 (MIME 스푸핑 방어)

---

### 🟠 H-02 — shifts 페이로드 크기 무제한
**파일:** `api/share.py`  
**상태:** ✅ 수정 완료 (커밋 5712fcc)

**내용:** POST `/api/share`의 `shifts` 딕셔너리 크기 제한 없음. 대용량 JSON으로 Supabase 스토리지 DoS 가능.

**수정:** `shifts` 직렬화 크기 50KB 초과 시 413 반환.

---

### 🟡 M-01 — row_index 범위 미검증
**파일:** `api/share.py`  
**상태:** ✅ 수정 완료 (커밋 5712fcc)

**내용:** `row_index`가 1~16 범위 밖인 음수, 거대한 수도 그대로 저장됨.

**수정:** `1 <= row_index <= 16` 범위 외 → 400 반환.

---

### 🟡 M-02 — existing_code 형식 미검증
**파일:** `api/share.py`  
**상태:** ✅ 수정 완료 (커밋 5712fcc)

**내용:** 클라이언트가 보내는 `code` 필드 길이·문자 제한 없음. 1000자 코드도 Supabase에 저장 시도.

**수정:** 6자리, `ABCDEFGHJKLMNPQRSTUVWXYZ23456789` 문자만 허용.

---

### 🟡 M-03 — 보안 응답 헤더 없음
**파일:** `vercel.json`  
**상태:** ✅ 수정 완료 (커밋 5712fcc)

**내용:** Clickjacking, MIME sniffing, Referrer 누출 방어 헤더 미설정.

**수정:** 추가된 헤더:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: microphone=(), geolocation=()`

---

### 🔵 L-01 — /api/ics `d` 파라미터 크기 무제한
**파일:** `api/ics.py`  
**상태:** ⬜ 미수정

**내용:** URL 파라미터 `?d=`로 임의 크기의 base64 전송 가능. 서버에서 디코딩 시 메모리 낭비.

**권장 수정:**
```python
data = request.args.get("d", "")
if len(data) > 100_000:  # ~75KB decoded
    return "데이터가 너무 큽니다", 413
```

---

### 🔵 L-02 — /api/ics 콘텐츠 인젝션
**파일:** `api/ics.py`  
**상태:** ⬜ 미수정

**내용:** 사용자가 `d` 파라미터로 임의 ICS 콘텐츠를 서비스 도메인에서 서빙 가능. `text/calendar` MIME이라 XSS 위험은 없으나 악성 캘린더 초대 배포에 악용 가능.

**권장 수정:** 디코딩 후 `BEGIN:VCALENDAR` 시작 여부 검증.
```python
if not ics_content.startswith("BEGIN:VCALENDAR"):
    return "잘못된 데이터", 400
```

---

## 양호 항목

| 항목 | 내용 |
|------|------|
| ✅ 시크릿 관리 | `credentials/` gitignore 처리, API 키 환경변수 사용 |
| ✅ SQL 인젝션 없음 | Supabase REST + `urllib.parse.quote` |
| ✅ XSS 없음 | `innerHTML`, `eval()`, `document.write` 미사용 |
| ✅ 파일 크기 제한 | `MAX_CONTENT_LENGTH = 10MB` |
| ✅ 코드 생성 안전 | `secrets.choice()` 사용 |
| ✅ SQL 파라미터 안전 | `urllib.parse.quote(code, safe="")` |

---

## 잔여 작업

없음 — 전 항목 조치 완료. (2026-07-10)

| ID | 파일 | 작업 | 상태 |
|----|------|------|------|
| L-01 | `api/ics.py`, `server.py` | `d` 파라미터 크기 제한 (100,000자) | ✅ 완료 |
| L-02 | `api/ics.py`, `server.py` | `BEGIN:VCALENDAR` 시작 여부 검증 | ✅ 완료 |
