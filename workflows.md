# Workflows

## 로컬 실행

1. 프로젝트 폴더로 이동한다.
2. `python3 server.py` 로 서버를 실행한다.
3. 브라우저에서 `http://localhost:3000` 으로 접속한다.

## 사진 가져오기

1. UI에서 근무표 사진을 선택한다.
2. 서버가 `/api/parse-duty` 로 사진을 받아 OCR 파싱을 수행한다.
3. 읽힌 행 데이터를 달력 상태에 반영한다.

## 코드 구조

1. [index.html](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/index.html)
   UI와 로컬 상태 관리
2. [server.py](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/server.py)
   정적 파일 서빙과 OCR API
3. [ocr/duty_parser.py](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/ocr/duty_parser.py)
   표 템플릿, 셀 박스 분할, OCR 후처리

## 개선 순서

1. 전체 표 외곽 자동 검출
2. 셀 박스 디버그 이미지 생성
3. 이름 인식 또는 행 자동 매칭 보강
4. 다른 월 양식 테스트
