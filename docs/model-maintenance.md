# Anthropic Model Maintenance

## 목적

Claude OCR이 오래된 모델 ID 때문에 깨지지 않도록, `ANTHROPIC_MODEL` 우선 + Anthropic 모델 API 자동 선택 + 짧은 fallback 순서로 운영한다.

## 점검 주기

- 월 1회 또는 API 오류 발생 시 즉시

## 확인할 것

1. 공식 Anthropic 모델 문서에서 현재 사용 가능한 모델명을 확인한다.
2. `.env` 또는 Vercel 환경변수의 `ANTHROPIC_MODEL` 값을 최신값으로 맞춘다.
3. `ocr/claude_parser.py`의 fallback 목록이 너무 오래되지 않았는지 확인한다.
4. 로컬 `py_compile`와 간단한 스모크 테스트를 다시 돌린다.
5. 배포 후 OCR 한 번을 실제로 통과시켜 본다.

## 현재 기준

- 기본값: `ANTHROPIC_MODEL`이 있으면 그 값, 없으면 최신 stable Sonnet 자동 선택
- fallback: `claude-sonnet-4-6`, `claude-opus-4-8`, `claude-haiku-4-5`

## 관련 파일

- [ocr/claude_parser.py](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/ocr/claude_parser.py)
- [.env.example](/Users/seungyobyi/Documents/Nurse%20Shift%20Board%20Prototype/.env.example)
