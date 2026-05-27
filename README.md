# Nurse Duty OCR Server

근무표 사진을 받아 듀티 데이터로 파싱하는 OCR API 서버.  
프론트(Vercel) ↔ OCR 서버(맥 미니) 구조로 운영합니다.

## 구조

```
Vercel (index.html)  ──POST /api/parse-duty──▶  맥 미니 (server.py)
                                                      │
                                               ocr/claude_parser.py
                                               Claude Vision API
```

## 빠른 시작

```bash
cp .env.example .env          # ANTHROPIC_API_KEY 설정
pip install -r requirements.txt
python server.py
```

`http://localhost:3000/health` 로 확인.

## 자주 쓰는 명령

```bash
# 서버 실행
source .venv/bin/activate && python server.py

# OCR 정확도 측정
python tools/score_ocr.py

# 방식별 벤치마크 (정확도/시간/토큰/비용)
python tools/benchmark_compare.py

# 문법 확인
python -m py_compile ocr/claude_parser.py ocr/duty_parser.py server.py
```

## 문서

- [배포 가이드](docs/deployment.md) — launchd, HTTPS, Vercel 연결
- [현재 상태](STATUS.md)
- [OCR 시도/실패/결론 기록](claude_ocr_findings.md)

## OCR 벤치마크 (주희_duty_june.jpeg, 480셀, 2026-05-27)

| 방식 | 정확도 | 시간 | 호출 | 비용 |
|------|--------|------|------|------|
| 베이스라인 (보조선 없음) | 97.1% (466/480) | 32.7s | 1 | $0.036 |
| **파란 보조선만 ← 채택** | **100% (480/480)** | 32.4s | 1 | $0.041 |
| 재판독만 row4 | 100% (480/480) | 37.3s | 2 | $0.045 |
| 파란 보조선 + 재판독 | 100% (480/480) | 38.0s | 2 | $0.050 |

시간·비용 동등, 1회 호출로 100% 달성 → 파란 보조선(`use_row_guides=True`)이 기본값.  
다른 방식 상세 분석: [claude_ocr_findings.md](claude_ocr_findings.md)

## 트러블슈팅

**사진 업로드 후 반영이 안 됨**
- `python server.py` 실행 여부 확인 → `http://localhost:3000` 으로 접속했는지 확인 (파일 경로 직접 열기 X)

**SSL 오류**
- `pip install certifi` 후 재시도

**git push 거부 (`failed to push some refs`)**
```bash
git pull --rebase origin main && git push origin main
```

## 파일 구조

```
├── index.html                  # 프론트엔드 UI (Vercel 배포)
├── server.py                   # 맥 미니 로컬 서버 (Flask)
├── api/
│   ├── parse-duty.py           # Vercel serverless OCR 엔드포인트
│   └── share.py                # 초대 코드 공유 엔드포인트
├── ocr/
│   ├── claude_parser.py        # Claude Vision OCR (메인)
│   ├── duty_parser.py          # 이미지 전처리, 표 감지, Tesseract 폴백
│   ├── google_vision_parser.py # Google Vision OCR (실험적)
│   └── _morphology.py          # 형태소 분석 유틸
├── tools/
│   ├── benchmark_compare.py    # 방식별 정확도/시간/비용 비교
│   └── score_ocr.py            # 샘플 기준 정확도 측정
├── docs/deployment.md          # 맥 미니 배포 가이드
├── STATUS.md                   # 현재 개발 상태
└── claude_ocr_findings.md      # OCR 실험 기록 (시도/실패/결론)
```

## 규칙

- OCR 로직은 `ocr/` 패키지에 모은다.
- `.env` 는 절대 커밋하지 않는다.
- 실험 파일은 `scratch/` 아래에만 둔다.
- 사진 양식이 바뀌면 `DEFAULT_TEMPLATE` 좌표부터 검증한다.
