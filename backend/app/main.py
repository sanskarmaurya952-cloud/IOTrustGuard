from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import cv2
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from ultralytics import YOLO


@dataclass
class AnalysisPoint:
    timestamp: str
    trust_score: float
    people_count: int
    max_person_confidence: float
    drift_score: float
    note: str


@dataclass
class Device:
    id: str
    name: str
    source: str
    source_type: str
    created_at: str
    last_analysis: AnalysisPoint | None = None
    history: list[AnalysisPoint] = field(default_factory=list)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class DeviceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=512)
    source_type: str = Field(pattern="^(webcam|ip|url)$")


class DeviceResponse(BaseModel):
    id: str
    name: str
    source: str
    source_type: str
    created_at: str
    last_analysis: dict[str, Any] | None


class AlertItem(BaseModel):
    timestamp: str
    device_id: str
    device_name: str
    severity: str
    message: str


class DataStore:
    def __init__(self) -> None:
        self.devices: dict[str, Device] = {}
        self.alerts: list[AlertItem] = []
        self.sessions: dict[str, dict[str, str]] = {}
        self.model: YOLO | None = None

    def get_model(self) -> YOLO:
        if self.model is None:
            self.model = YOLO(os.getenv("YOLO_MODEL_PATH", "yolov8n.pt"))
        return self.model


store = DataStore()
auth_scheme = HTTPBearer()
MAX_ALERTS = 200
MAX_HISTORY_POINTS = 300
SESSION_TTL_MINUTES = int(os.getenv("SESSION_TTL_MINUTES", "60"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not store.devices:
        dev_id = str(uuid.uuid4())
        store.devices[dev_id] = Device(
            id=dev_id,
            name="Local Webcam",
            source="0",
            source_type="webcam",
            created_at=now_iso(),
        )
    yield


app = FastAPI(title="IOTrustGuard API", version="1.0.0", lifespan=lifespan)

allowed_origins = [
    origin.strip() for origin in os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR)), name="assets")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_source(device: Device) -> str | int:
    if device.source_type == "webcam":
        try:
            return int(device.source)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid webcam index.") from exc
    return device.source


def authenticate(credentials: HTTPAuthorizationCredentials = Depends(auth_scheme)) -> str:
    token = credentials.credentials
    session = store.sessions.get(token)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")
    expires_at = datetime.fromisoformat(session["expires_at"])
    if datetime.now(UTC) >= expires_at:
        store.sessions.pop(token, None)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")
    return session["username"]


def push_alert(device: Device, severity: str, message: str) -> None:
    store.alerts.insert(
        0,
        AlertItem(
            timestamp=now_iso(),
            device_id=device.id,
            device_name=device.name,
            severity=severity,
            message=message,
        ),
    )
    del store.alerts[MAX_ALERTS:]


def analyze_device(device: Device) -> AnalysisPoint:
    capture = cv2.VideoCapture(parse_source(device))
    try:
        if not capture.isOpened():
            raise HTTPException(status_code=400, detail="Could not open device stream.")

        ok, frame = capture.read()
        if not ok or frame is None:
            raise HTTPException(status_code=400, detail="Could not read frame from device stream.")
    finally:
        capture.release()

    model = store.get_model()
    results = model.predict(frame, verbose=False, classes=[0])[0]

    people_count = 0
    max_conf = 0.0
    for box in results.boxes:
        cls_id = int(box.cls[0].item())
        if cls_id != 0:
            continue
        conf = float(box.conf[0].item())
        people_count += 1
        max_conf = max(max_conf, conf)

    previous = device.last_analysis.trust_score if device.last_analysis else 50.0
    if people_count > 0:
        trust = min(100.0, 45.0 + (max_conf * 55.0))
        note = "Human verified by YOLO."
    else:
        trust = 15.0
        note = "No human detected by YOLO."

    trust = round((0.7 * trust) + (0.3 * previous), 2)
    drift = round(abs(trust - previous), 2)

    point = AnalysisPoint(
        timestamp=now_iso(),
        trust_score=trust,
        people_count=people_count,
        max_person_confidence=round(max_conf, 4),
        drift_score=drift,
        note=note,
    )

    device.last_analysis = point
    device.history.append(point)
    device.history = device.history[-MAX_HISTORY_POINTS:]

    if people_count == 0:
        push_alert(device, "high", "No person detected from camera feed.")
    elif trust < 55:
        push_alert(device, "medium", f"Trust score dropped to {trust}.")
    elif drift >= 25:
        push_alert(device, "low", f"Trust score drift detected: {drift}.")

    return point


@app.post("/api/auth/login")
def login(request: LoginRequest) -> dict[str, str]:
    expected_user = os.getenv("ADMIN_USERNAME", "admin")
    expected_pass = os.getenv("ADMIN_PASSWORD", "admin123")
    if request.username != expected_user or request.password != expected_pass:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")
    token = str(uuid.uuid4())
    expires_at = datetime.now(UTC) + timedelta(minutes=SESSION_TTL_MINUTES)
    store.sessions[token] = {
        "username": request.username,
        "expires_at": expires_at.isoformat(),
    }
    return {"token": token, "username": request.username}


@app.post("/api/auth/logout")
def logout(credentials: HTTPAuthorizationCredentials = Depends(auth_scheme)) -> dict[str, str]:
    store.sessions.pop(credentials.credentials, None)
    return {"status": "ok"}


@app.get("/api/devices", response_model=list[DeviceResponse])
def list_devices(_: str = Depends(authenticate)) -> list[DeviceResponse]:
    return [
        DeviceResponse(
            id=d.id,
            name=d.name,
            source=d.source,
            source_type=d.source_type,
            created_at=d.created_at,
            last_analysis=asdict(d.last_analysis) if d.last_analysis else None,
        )
        for d in store.devices.values()
    ]


@app.post("/api/devices", response_model=DeviceResponse)
def create_device(request: DeviceCreateRequest, _: str = Depends(authenticate)) -> DeviceResponse:
    dev_id = str(uuid.uuid4())
    device = Device(
        id=dev_id,
        name=request.name.strip(),
        source=request.source.strip(),
        source_type=request.source_type,
        created_at=now_iso(),
    )
    store.devices[dev_id] = device
    return DeviceResponse(
        id=device.id,
        name=device.name,
        source=device.source,
        source_type=device.source_type,
        created_at=device.created_at,
        last_analysis=None,
    )


@app.post("/api/devices/bootstrap-webcam", response_model=DeviceResponse)
def bootstrap_webcam(_: str = Depends(authenticate)) -> DeviceResponse:
    for dev in store.devices.values():
        if dev.source_type == "webcam" and dev.source == "0":
            return DeviceResponse(
                id=dev.id,
                name=dev.name,
                source=dev.source,
                source_type=dev.source_type,
                created_at=dev.created_at,
                last_analysis=asdict(dev.last_analysis) if dev.last_analysis else None,
            )
    dev_id = str(uuid.uuid4())
    device = Device(
        id=dev_id,
        name="Local Webcam",
        source="0",
        source_type="webcam",
        created_at=now_iso(),
    )
    store.devices[dev_id] = device
    return DeviceResponse(
        id=device.id,
        name=device.name,
        source=device.source,
        source_type=device.source_type,
        created_at=device.created_at,
        last_analysis=None,
    )


@app.post("/api/devices/{device_id}/analyze")
def analyze(device_id: str, _: str = Depends(authenticate)) -> dict[str, Any]:
    device = store.devices.get(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")
    point = analyze_device(device)
    return {"device_id": device.id, "device_name": device.name, "analysis": asdict(point)}


@app.get("/api/devices/{device_id}/history")
def history(
    device_id: str,
    limit: int = Query(default=50, ge=1, le=300),
    _: str = Depends(authenticate),
) -> list[dict[str, Any]]:
    device = store.devices.get(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")
    return [asdict(point) for point in device.history[-limit:]]


@app.get("/api/alerts", response_model=list[AlertItem])
def list_alerts(limit: int = Query(default=50, ge=1, le=200), _: str = Depends(authenticate)) -> list[AlertItem]:
    return store.alerts[:limit]


@app.get("/api/dashboard")
def dashboard(_: str = Depends(authenticate)) -> dict[str, Any]:
    latest = [d.last_analysis.trust_score for d in store.devices.values() if d.last_analysis]
    avg_trust = round(sum(latest) / len(latest), 2) if latest else 0.0
    return {
        "total_devices": len(store.devices),
        "analyzed_devices": len(latest),
        "average_trust_score": avg_trust,
        "active_alerts": len(store.alerts),
    }


@app.get("/")
def frontend_index() -> FileResponse:
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found.")
    return FileResponse(index_path)


@app.get("/{file_name}")
def frontend_file(file_name: str) -> FileResponse:
    file_path = FRONTEND_DIR / file_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Not found.")
    return FileResponse(file_path)
