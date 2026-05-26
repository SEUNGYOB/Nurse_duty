# Errors

## 사진 업로드는 했는데 반영이 안 됨

원인
- `server.py` 를 실행하지 않고 `index.html` 만 직접 연 경우
- OCR이 거의 읽지 못한 경우

대응
1. `python3 server.py` 로 서버를 다시 실행한다.
2. `http://localhost:3000` 으로 다시 접속한다.
3. 업로드 후 인식 개수 메시지를 확인한다.

## `failed to push some refs`

원인
- 원격 `main` 에 로컬에 없는 커밋이 있음

대응
```bash
git fetch origin
git pull --rebase origin main
git push -u origin main
```

## `server.py` 가 GitHub에 없음

원인
- 파일이 실제로 로컬 폴더에서 빠졌거나
- `git add` 전에 생성만 하고 커밋하지 않았음

대응
1. `ls server.py` 로 파일 존재 여부 확인
2. `git status --short` 확인
3. `git add . && git commit && git push`

## `localhost` 에서 사진 OCR이 동작하지 않음

원인
- 브라우저가 서버 주소가 아닌 파일 경로로 열림

대응
- 반드시 `http://localhost:3000` 으로 접속
