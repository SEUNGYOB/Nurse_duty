# Examples

## 샘플 파일

- `주희_duty_june.jpeg`

## 기대 흐름

1. 사진 업로드
2. `2026년 6월` 로 월 설정
3. 각 이름 행의 `D / E / N / S / Y / OFF` 를 읽음
4. 달력 셀에 이름별 듀티 표시

## API 응답 예시

```json
{
  "year": 2026,
  "month": 6,
  "rows": [
    {
      "rowIndex": 1,
      "name": "윤미영",
      "recognizedDays": 9,
      "shifts": [null, null, "off"]
    }
  ]
}
```
