# QuizLAN Web

Ứng dụng thi trắc nghiệm Flask (học sinh + admin).

## Cách chạy local
```bash
pip install -r requirements.txt
python app.py
```
Mở http://127.0.0.1:5000

## Deploy Render/Deta/Fly.io
- Render: cần file `requirements.txt` và `Procfile`
- Start command: `gunicorn app:app`
