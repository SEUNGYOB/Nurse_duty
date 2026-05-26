# Nurse Duty Import Skill

## 목적

근무표 사진을 읽어서 간호사 듀티 달력에 반영하는 로컬 프로토타입입니다.

## 언제 쓰는지

- 엑셀 스타일 근무표 사진을 달력 데이터로 옮기고 싶을 때
- 로컬 맥에서 파이썬 OCR 서버와 정적 HTML UI를 함께 실행할 때
- `D / E / N / S / Y / OFF` 같은 짧은 근무 코드를 읽을 때

## 사용하지 말아야 할 경우

- Google Calendar 연동이나 배포용 서비스가 바로 필요한 경우
- 사진 양식이 현재 샘플과 크게 다른 경우
- 서버 없이 파일 하나만 열어서 완전히 동작하길 기대하는 경우

## 입력값

- 근무표 사진 파일
- 브라우저에서 선택한 팀원 또는 자동 생성될 팀원 이름

## 출력 형식

- 브라우저 달력에 반영된 월별 듀티 데이터
- `/api/parse-duty` JSON 응답

## 먼저 볼 문서

- [STATUS.md](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/STATUS.md)
- [workflows.md](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/workflows.md)
- [commands.md](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/commands.md)
- [errors.md](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/errors.md)
- [FUTURE_FEATURES.md](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/FUTURE_FEATURES.md)

## 반드시 지킬 규칙

- 브라우저는 `index.html` 직접 열지 말고 `server.py`로 띄운 주소로 접속한다.
- OCR 로직은 [ocr/duty_parser.py](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/ocr/duty_parser.py) 에 모은다.
- README에는 긴 설명을 넣지 않고 세부 설명은 분리 문서에 둔다.
- 디버그용 산출물은 커밋 대상이 아니라 필요 시 재생성한다.
- 실험 이미지와 임시 파일은 `scratch/` 아래에만 둔다.
- 사진 양식이 바뀌면 템플릿 좌표부터 검증한다.
