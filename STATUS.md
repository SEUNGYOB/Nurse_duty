# Status

## 현재 상태

- HTML UI는 [index.html](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/index.html) 에 있다.
- 로컬 서버는 [server.py](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/server.py) 가 담당한다.
- OCR 파서는 [ocr/duty_parser.py](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/ocr/duty_parser.py) 로 분리되어 있다.
- 디버그 산출물은 `scratch/ocr-debug/` 로 분리했다.

## 동작 범위

- 샘플 엑셀 스타일 근무표 사진 기준으로 동작
- `D / E / N / S / Y / OFF` 코드 파싱 시도
- 업로드 후 달력 반영 흐름 존재

## 남은 과제

- 표 외곽 자동 검출
- 셀 OCR 정확도 개선
- 팀원 이름 자동 매칭 안정화
- 다른 달/다른 사진 각도 테스트
