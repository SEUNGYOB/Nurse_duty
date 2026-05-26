# Nurse Duty OCR Server

근무표 사진을 받아 듀티 데이터로 파싱하는 OCR API 서버입니다.  
프론트(Vercel) ↔ OCR 서버(맥 미니) 구조로 운영합니다.

## 구조

```
Vercel (index.html)  ──POST /api/parse-duty──▶  맥 미니 (server.py)
                                                      │
                                                  ocr/duty_parser.py
                                                  Tesseract OCR
```

## 빠른 시작

```bash
cp .env.example .env          # 환경변수 설정
pip install -r requirements.txt
python server.py
```

`http://localhost:3000/health` 로 확인.

## 문서

- [배포 및 외부 공개 가이드](docs/deployment.md) — launchd, HTTPS, Vercel 연결, 체크리스트
- [STATUS.md](STATUS.md)
- [workflows.md](workflows.md)
- [errors.md](errors.md)

## 규칙

- OCR 로직은 `ocr/duty_parser.py` 에 모은다.
- `.env` 는 절대 커밋하지 않는다.
- 실험 파일은 `scratch/` 아래에만 둔다.
- 사진 양식이 바뀌면 템플릿 좌표부터 검증한다.
