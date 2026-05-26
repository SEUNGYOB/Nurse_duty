# Commands

## 서버 실행

```bash
cd "/Users/seungyobyi/Documents/Nurse Shift Board Prototype"
python3 server.py
```

## 문법 확인

```bash
python3 -m py_compile server.py ocr/duty_parser.py ocr/__init__.py
```

## Git 반영

```bash
git add .
git commit -m "Update nurse duty import flow"
git push -u origin main
```

## 원격 최신 반영 후 푸시

```bash
git fetch origin
git pull --rebase origin main
git push -u origin main
```
