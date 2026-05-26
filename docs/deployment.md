# 서버 배포 가이드

맥 미니에서 OCR API 서버를 외부에 공개하는 절차입니다.

## 전제 조건

- Python 3.12
- Tesseract OCR (`brew install tesseract`)
- 고정 IP 또는 DDNS 설정 완료

---

## 1. 환경 설정

```bash
cd /Users/moltbot/projects/Nurse_duty

# 가상환경 생성 (처음 한 번)
python3.12 -m venv .venv
source .venv/bin/activate

# 패키지 설치
pip install -r requirements.txt

# 환경변수 파일 생성
cp .env.example .env
```

`.env` 파일을 열어 아래 값을 채운다.

```
ALLOWED_ORIGINS=https://your-app.vercel.app
API_TOKEN=<openssl rand -hex 32 결과>
```

---

## 2. 수동 실행

```bash
source .venv/bin/activate
python server.py
```

정상 시 `Serving on http://0.0.0.0:3000` 출력.  
`/health` 엔드포인트로 확인: `curl http://localhost:3000/health`

---

## 3. launchd 서비스 등록 (자동 시작)

아래 내용을 `~/Library/LaunchAgents/com.nurseduty.server.plist` 에 저장한다.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.nurseduty.server</string>

  <key>ProgramArguments</key>
  <array>
    <string>/Users/moltbot/projects/Nurse_duty/.venv/bin/python</string>
    <string>/Users/moltbot/projects/Nurse_duty/server.py</string>
  </array>

  <key>WorkingDirectory</key>
  <string>/Users/moltbot/projects/Nurse_duty</string>

  <key>EnvironmentVariables</key>
  <dict>
    <!-- .env 파일이 load_dotenv()로 읽히므로 여기서는 경로만 보조 설정 -->
    <key>HOME</key>
    <string>/Users/moltbot</string>
  </dict>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>/tmp/nurseduty.log</string>

  <key>StandardErrorPath</key>
  <string>/tmp/nurseduty.err</string>
</dict>
</plist>
```

등록 및 시작:

```bash
launchctl load ~/Library/LaunchAgents/com.nurseduty.server.plist

# 상태 확인
launchctl list | grep nurseduty

# 로그 확인
tail -f /tmp/nurseduty.log /tmp/nurseduty.err
```

중지 및 해제:

```bash
launchctl unload ~/Library/LaunchAgents/com.nurseduty.server.plist
```

---

## 4. 외부 공개 전 체크리스트

- [ ] `.env`에 `API_TOKEN` 설정 (`openssl rand -hex 32`)
- [ ] `.env`에 `ALLOWED_ORIGINS`를 Vercel 도메인으로 제한
- [ ] 공유기에서 PORT(기본 3000) → 맥 미니 내부 IP 포트포워딩 설정
- [ ] macOS 방화벽에서 PORT 허용 (시스템 설정 → 방화벽)
- [ ] DDNS 또는 고정 IP로 외부 접근 주소 확정
- [ ] `curl https://your-domain/health` 로 외부 접근 확인
- [ ] HTTPS 사용 시: nginx 리버스 프록시 + Let's Encrypt 설정 (아래 참고)

### HTTPS 옵션 (권장)

Vercel(HTTPS)에서 HTTP 서버를 직접 호출하면 브라우저가 Mixed Content로 차단합니다.  
아래 중 하나를 선택하세요.

**A. Cloudflare Tunnel (가장 간단)**

```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:3000
# 할당된 *.trycloudflare.com 주소를 Vercel 환경변수에 사용
```

**B. nginx + Let's Encrypt**

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    client_max_body_size 12M;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## 5. Vercel 연결

### 구조

```
브라우저 (Vercel)
  └─ POST /api/parse-duty
       └─ Vercel 함수 (api/parse-duty.js)  ← Mac Mini URL·토큰을 여기서 보관
            └─ POST <OCR_API_URL>/api/parse-duty  → 맥 미니 서버
```

브라우저는 Mac Mini를 직접 알 필요가 없습니다. Vercel 함수가 프록시 역할을 합니다.  
HTTPS 문제(Mixed Content)도 Vercel 함수가 자동으로 해결합니다.

### Vercel 환경변수 설정

Vercel 프로젝트 → Settings → Environment Variables 에 추가:

| 변수 | 예시 값 | 설명 |
|------|---------|------|
| `OCR_API_URL` | `https://xxxx.trycloudflare.com` | 맥 미니 외부 주소 |
| `OCR_API_TOKEN` | `<API_TOKEN 값>` | server.py의 API_TOKEN과 동일 |

### 배포 절차

```bash
# Vercel CLI 설치 (처음 한 번)
npm i -g vercel

# 프로젝트 루트에서
vercel

# 이후 변경사항 배포
vercel --prod
```

또는 GitHub 저장소를 Vercel에 연결하면 main 브랜치 push 시 자동 배포됩니다.

### 배포 후 확인

```bash
# Vercel 함수가 맥 미니에 연결되는지 확인
curl https://your-app.vercel.app/api/parse-duty -X POST
# → {"error":"사진 파일을 찾지 못했어요."} 가 나오면 연결 성공
```

---

## 6. 남은 리스크

| 항목 | 위험도 | 대응 |
|------|--------|------|
| HTTP 평문 전송 | 높음 | HTTPS 필수 (Cloudflare Tunnel 권장) |
| IP 변경 | 중간 | DDNS 서비스 사용 |
| 서버 다운 시 자동 복구 없음 | 중간 | launchd KeepAlive=true 로 자동 재시작 |
| API_TOKEN 탈취 | 중간 | 주기적 로테이션, 로그 모니터링 |
| Tesseract 미설치 | 낮음 | `which tesseract` 로 확인 후 `brew install tesseract` |
