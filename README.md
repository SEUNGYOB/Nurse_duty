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
- [Claude OCR Findings](claude_ocr_findings.md)
- [workflows.md](workflows.md)
- [errors.md](errors.md)

## 현재 OCR 메모

### 벤치마크 결과 (샘플: 주희_duty_june.jpeg, 480셀, 2026-05-27)

| 방식 | 정확도 | 시간 | API 호출 | 입력 토큰 | 출력 토큰 | 비용 |
|------|--------|------|----------|-----------|-----------|------|
| 베이스라인 (보조선 없음, 재판독 없음) | 97.1% (466/480) | 32.7s | 1 | 2,324 | 1,955 | $0.0363 |
| **파란 보조선만 (1차 호출)** | **100% (480/480)** | 32.4s | 1 | 3,960 | 1,955 | $0.0412 |
| 재판독만 row4 (보조선 없음) | 100% (480/480) | 37.3s | 2 | 4,554 | 2,107 | $0.0453 |
| 파란 보조선 + 재판독 row4 | 100% (480/480) | 38.0s | 2 | 6,190 | 2,107 | $0.0502 |

### 구조

- 1차: 전체 표 이미지를 Claude에 1회 호출 → 16행 전체 파싱
- 2차: 문제 행만 `refine_row_indices`로 재판독 → 강조 이미지 + focused crop 두 장 전송

### 결론

- **파란 보조선만으로 100% 달성 가능** — 추가 API 호출 없이 $0.0049 비용 증가만으로 해결
- 재판독(`refine_row_indices`)은 보조선보다 느리고 비용도 높으나 보조선이 없는 환경에서도 100% 가능
- 두 방법을 조합해도 정확도는 동일 → 보조선만 사용하는 것이 최적
- 다음 과제: 1차 결과에서 null이 많은 행을 자동 감지해 서버에서 자동 재판독 트리거
- 세로 해석과 가로/세로 교차검증은 실패로 확정, 중단
- 자세한 시도/실패 기록은 [claude_ocr_findings.md](claude_ocr_findings.md) 참조

## 규칙

- OCR 로직은 `ocr/duty_parser.py` 에 모은다.
- `.env` 는 절대 커밋하지 않는다.
- 실험 파일은 `scratch/` 아래에만 둔다.
- 사진 양식이 바뀌면 템플릿 좌표부터 검증한다.
