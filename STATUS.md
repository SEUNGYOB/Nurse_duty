# Status

## 현재 상태

- HTML UI는 [index.html](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/index.html) 에 있다.
- 로컬 서버는 [server.py](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/server.py) 가 담당한다.
- OCR 파서는 [ocr/duty_parser.py](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/ocr/duty_parser.py) 로 분리되어 있다.
- 디버그 산출물은 `scratch/ocr-debug/` 로 분리했다.
- 현재 기본 OCR 경로는 Claude Vision (`use_row_guides=True`) 이다.
- Claude 모델은 `ANTHROPIC_MODEL` 우선, 없으면 Anthropic 모델 API에서 최신 stable Sonnet을 자동 선택한다. 유지보수 문서는 [docs/model-maintenance.md](docs/model-maintenance.md).
- 파란 보조선 방식으로 **100% 정확도 (480/480)** 달성 (2026-05-27 확정).
- 세로 해석과 가로/세로 교차검증은 실패로 확정, 중단했다.
- 시도/실패/채택 기록은 [claude_ocr_findings.md](claude_ocr_findings.md) 에 정리했다.

## 동작 범위

- 샘플 엑셀 스타일 근무표 사진 기준으로 동작
- `D / E / N / S / Y / OFF` 코드 파싱 시도
- 업로드 후 달력 반영 흐름 존재

## 남은 과제

- 표 외곽 자동 검출
- 팀원 이름 자동 매칭 안정화
- 다른 달/다른 병원 양식 테스트
- Anthropic 모델명 정기 갱신
