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

### 벤치마크 결과 (샘플: 주희_duty_june.jpeg, 480셀)

| 방식 | 정확도 | 비고 |
|------|--------|------|
| Claude Vision 1회 호출 | 97.1% (466/480) | 오답 전부 장혜진 행 |
| **Claude Vision + 재판독 (rowIndex=4)** | **100% (480/480)** | 장혜진 행 재판독으로 완전 해결 |

### 구조

- 1차: 전체 표 이미지를 Claude에 1회 호출 → 16행 전체 파싱
- 2차: 문제 행만 `refine_row_indices`로 재판독 → 강조 이미지 + focused crop 두 장 전송

### 결론

- 재판독 루프(`refine_row_indices`)가 100% 정확도를 달성함 (2026-05-27 확인)
- 다음 과제: 1차 결과에서 null이 많은 행을 자동 감지해 서버에서 자동 재판독 트리거
- 세로 해석과 가로/세로 교차검증은 실패로 확정, 중단
- 자세한 시도/실패 기록은 [claude_ocr_findings.md](claude_ocr_findings.md) 참조

## 규칙

- OCR 로직은 `ocr/duty_parser.py` 에 모은다.
- `.env` 는 절대 커밋하지 않는다.
- 실험 파일은 `scratch/` 아래에만 둔다.
- 사진 양식이 바뀌면 템플릿 좌표부터 검증한다.
