# IOTrustGuard - IoT Trust & Drift Analytics

Full working project with:
- **Frontend**: Login page + dashboard, add IoT devices by IP/URL, alerts, trust score cards, and trust graph.
- **Backend**: Device management, webcam bootstrap, YOLO human verification, trust-score/drift analytics, alerts.

## Project Structure

```
backend/
  app/main.py
  requirements.txt
frontend/
  index.html
  styles.css
  script.js
```

## Features

1. Login and authenticated API usage.
2. Add IoT devices (IP stream URL, generic URL stream, webcam index).
3. Auto-add "Local Webcam" (index `0`) and manual "Add My Webcam" option.
4. YOLO (`yolov8n`) human verification from camera frames.
5. Trust score and drift score calculation.
6. Alert generation for no-human detection or trust drop.
7. Dashboard summary + line graph of trust history.

## Run Locally

### 1) Backend setup

```bash
cd /home/runner/work/IOTrustGuard/IOTrustGuard
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

### 2) Start application

```bash
cd /home/runner/work/IOTrustGuard/IOTrustGuard
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

Open: `http://localhost:8000`

### 3) Default login

- Username: `admin`
- Password: `admin123`

You can override with env vars:

```bash
export ADMIN_USERNAME=your_user
export ADMIN_PASSWORD=your_password
```

## Notes

- For webcam access in cloud/VM, make sure camera is available to the runtime.
- For IP camera, use a valid stream URL (for example RTSP/HTTP stream endpoint).
- The first YOLO run may download model weights (`yolov8n.pt`).
