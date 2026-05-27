# Nurse Shift Board — TODO & 현황

## 현재 배포 상태
- **Vercel**: https://project-cvl1r.vercel.app
- **GitHub**: https://github.com/SEUNGYOB/Nurse_duty
- **Branch**: main

---

## 완료된 주요 기능

### OCR (근무표 인식)
- [x] Claude Vision API로 근무표 사진 인식 (100% 정확도)
- [x] 파란 행 가이드라인으로 정확도 향상
- [x] SSE 스트리밍으로 실시간 진행 상태 표시
- [x] 2단계 UX: **인식하기** → 이름 드롭다운 표시 → **가져오기**
- [x] 한 번 인식으로 여러 명 임포트 가능 (결과 유지)
- [x] 진행 메시지 한국어 감성으로 변경 ("AI가 듀티 보는 중... 잠깐만요" 등)

### 보안 / 프라이버시
- [x] HTML 소스에서 실명 완전 제거
- [x] `names.json` gitignore 처리 (실명은 로컬에만)
- [x] rowIndex를 유일 식별자로 사용 (이름은 표시용 메타데이터)
- [x] 마스킹된 이미지만 학습 데이터로 저장 (이름 컬럼 블랙아웃)
- [x] 업로드 원본 사진은 분석 후 즉시 메모리에서 삭제

### 학습 데이터 수집 파이프라인
- [x] `ocr/training_store.py`: 마스킹 이미지 → Supabase Storage
- [x] OCR 결과 (rowIndex + shifts만, 이름 제외) → `training_samples` DB
- [x] 백그라운드 스레드 처리 (OCR 속도에 영향 없음)
- [x] 개인정보 고지 문구 표시

### 공유 기능
- [x] 팀원별 공유 코드 생성 (Supabase `duty_rooms` 테이블)
- [x] 코드/링크 복사 → 카카오톡·문자 공유
- [x] 링크로 상대방 듀티 자동 불러오기
- [x] 공유할 팀원 드롭다운 선택 (누구 것을 보낼지 명확하게)
- [x] **팀원별 코드 영구 유지** + upsert: 같은 링크로 최신 듀티 항상 반영
- [x] 하단 플로팅 토스트 알림 (복사/완료 등)

### UX / PWA
- [x] PWA 지원 (설치, 서비스워커, 오프라인)
- [x] SW 네트워크 우선 전략 (HTML은 항상 최신, 배포 후 강새로고침 불필요)
- [x] 주간 달력 레이아웃 (멤버 이름 왼쪽)
- [x] 시작 날짜 선택 후 순차 입력 방식

### 인프라
- [x] Vercel 배포 (Python Flask + 정적 파일)
- [x] Supabase (Storage + DB) 연동
- [x] `maxDuration: 120s` (타임아웃 방지)
- [x] Mac mini 로컬 서버 (`server.py`)

---

## 남은 작업 / 개선 아이디어

### 버그 / 안정성
- [ ] OCR 네트워크 오류 재현 시 원인 추가 파악 (cold start 타임아웃 가능성 남아있음)
- [ ] 공유 링크 클릭 후 자동 join이 로컬 state와 충돌하는 케이스 검토
- [ ] `duty_rooms` 테이블 오래된 레코드 정리 정책 없음 (TTL 설정 필요)

### 기능 추가
- [ ] **월 이동 UI**: 이전/다음 달 버튼 또는 스와이프
- [ ] **듀티 통계**: 근무 유형별 집계 (D/N/E/OFF 각 몇 일)
- [ ] **반복 패턴 자동 채우기**: D-D-N-N-E-E-OFF 같은 패턴 반복 입력
- [ ] **OCR 수동 수정 UI**: 인식 결과 틀렸을 때 셀 직접 수정
- [ ] **상대방 이름 커스텀**: 공유 코드로 불러올 때 "상대방" 대신 이름 지정
- [ ] **다크모드**

### 학습 데이터 / 모델
- [ ] 수집된 `training_samples` 데이터 검토 및 정제 도구 개발
- [ ] Fine-tuning 또는 프롬프트 개선을 위한 벤치마크 재실행
- [ ] Google Vision API 비교 결과 반영 여부 결정

### 배포 / 운영
- [ ] 커스텀 도메인 연결
- [ ] App Store / Play Store 등록 (PWA → 네이티브 래퍼 고려)
- [ ] Vercel cold start 최소화 (Fluid Compute 또는 keep-warm ping)
- [ ] ANTHROPIC_API_KEY 사용량 모니터링

### 코드 정리
- [ ] `tools/` 폴더 벤치마크 스크립트들 정리 (실험용 파일 다수)
- [ ] `ocr/duty_parser.py` (Tesseract) 유지 여부 결정 — 현재 미사용
- [ ] `api/parse-duty.py` vs `server.py` 중복 로직 통합 검토

---

## Supabase 테이블 현황

```sql
-- 공유 방
CREATE TABLE duty_rooms (
  code TEXT PRIMARY KEY,
  row_index INTEGER NOT NULL,
  shifts JSONB NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 학습 데이터
CREATE TABLE training_samples (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now(),
  image_path TEXT NOT NULL,  -- training-images 버킷 경로
  ocr_result JSONB NOT NULL  -- {year, month, rows: [{rowIndex, shifts}]}
);
ALTER TABLE training_samples ENABLE ROW LEVEL SECURITY;
```

## 환경변수 (Vercel + .env)
```
ANTHROPIC_API_KEY=...
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...
```
