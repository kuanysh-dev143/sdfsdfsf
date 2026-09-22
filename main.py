"""
main.py
--------
FastAPI backend for the AI Fraud Detection System (demo/sandbox).

Security notes:
- No real payment data (PAN, CVV, PIN) is ever accepted or stored.
- All inputs are validated via Pydantic models.
- SQLite access goes exclusively through parameterized queries in database.py
  (no string-built SQL), which prevents SQL injection.
- Request bodies are size-limited by FastAPI/Starlette + Pydantic field constraints.
- Secrets (if any were needed) would be loaded from a .env file - see .env.example.
"""

import asyncio
import json
import random
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

import database
import fraud_model

app = FastAPI(
    title="AI Fraud Detection System (Demo/Sandbox)",
    description="Real-time fraud risk scoring MVP. Uses only synthetic/test data.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # demo only - restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_MODELS = None  # lazily loaded (rf, iso) tuple


def get_models():
    global _MODELS
    if _MODELS is None:
        _MODELS = fraud_model.get_model()
    return _MODELS


@app.on_event("startup")
def on_startup():
    database.init_db()
    get_models()  # trains models on first run if not cached on disk
    database.seed_demo_data()


# ---------------------------------------------------------------------------
# Pydantic models (input validation)
# ---------------------------------------------------------------------------

class TransactionInput(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)
    amount: float = Field(..., gt=0, le=100_000_000)
    tx_time: Optional[str] = Field(None, description="ISO 8601 timestamp")
    location: str = Field("Astana", max_length=64)
    device_id: str = Field(..., min_length=1, max_length=64)
    previous_transactions_count: int = Field(0, ge=0, le=1_000_000)
    tx_last_10min: int = Field(0, ge=0, le=1000)
    is_new_device: bool = False
    distance_from_prev_location_km: float = Field(0, ge=0, le=40075)

    @field_validator("user_id", "device_id", "location")
    @classmethod
    def no_control_chars(cls, v: str) -> str:
        if any(ord(c) < 32 for c in v):
            raise ValueError("invalid characters in field")
        return v.strip()

    def to_payload(self) -> dict:
        return {
            "user_id": self.user_id,
            "amount": self.amount,
            "tx_time": self.tx_time or datetime.utcnow().isoformat(),
            "location": self.location,
            "device_id": self.device_id,
            "is_new_device": self.is_new_device,
            "previous_tx_count": self.previous_transactions_count,
            "tx_last_10min": self.tx_last_10min,
            "distance_from_prev_km": self.distance_from_prev_location_km,
        }


class AnalyzeResponse(BaseModel):
    tx_id: str
    risk_score: float
    risk_level: str
    fraud_probability: float
    anomaly_detected: bool
    reasons: list[str]


# ---------------------------------------------------------------------------
# Core analysis helper
# ---------------------------------------------------------------------------

def _analyze_and_store(payload: dict) -> dict:
    models = get_models()
    feature_row, profile = fraud_model.build_feature_row(payload)
    result = fraud_model.score_transaction(models, feature_row, profile, payload)
    tx_id = database.insert_transaction(payload, result)
    if payload.get("device_id"):
        database.register_device(payload["device_id"], payload["user_id"])
    result["tx_id"] = tx_id
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat(), "service": "fraud-detection-api"}


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze_transaction(tx: TransactionInput):
    try:
        result = _analyze_and_store(tx.to_payload())
        return result
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=400, detail=f"Analysis failed: {exc}")


@app.post("/api/transactions", response_model=AnalyzeResponse)
def create_transaction(tx: TransactionInput):
    """Alias of /api/analyze - adds a transaction and returns its risk analysis."""
    return analyze_transaction(tx)


@app.get("/api/transactions")
def get_transactions(limit: int = 100):
    limit = max(1, min(limit, 500))
    rows = database.list_transactions(limit=limit)
    return {"transactions": rows, "count": len(rows)}


@app.get("/api/statistics")
def statistics():
    return database.get_statistics()


@app.get("/api/network")
def network():
    return database.get_network_graph()


# ---------------------------------------------------------------------------
# Live simulation (WebSocket)
# ---------------------------------------------------------------------------

_DEMO_USERS = ["USER-1001", "USER-1024", "USER-1077", "USER-2051", "USER-2099"]
_DEMO_LOCATIONS = ["Astana", "Almaty", "Shymkent", "Karaganda", "Aktobe"]
_DEMO_DEVICES = ["Device-A21", "Device-B07", "Device-C14", "Device-D33"]


def _generate_random_transaction() -> dict:
    is_suspicious = random.random() < 0.28
    user_id = random.choice(_DEMO_USERS)
    base_amount = random.uniform(4000, 40000)
    amount = base_amount * random.uniform(8, 30) if is_suspicious else base_amount
    hour = random.choice([1, 2, 3, 4]) if is_suspicious else random.randint(8, 22)
    now = datetime.utcnow().replace(hour=hour % 24)
    device = f"New-Device-{random.randint(100,999)}" if is_suspicious else random.choice(_DEMO_DEVICES)

    return {
        "user_id": user_id,
        "amount": round(amount, 2),
        "tx_time": now.isoformat(),
        "location": random.choice(_DEMO_LOCATIONS),
        "device_id": device,
        "is_new_device": is_suspicious,
        "previous_tx_count": random.randint(0, 60),
        "tx_last_10min": random.randint(4, 9) if is_suspicious else random.randint(0, 2),
        "distance_from_prev_km": random.uniform(200, 2500) if is_suspicious else random.uniform(0, 20),
    }


@app.websocket("/ws/live")
async def live_simulation(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            payload = _generate_random_transaction()
            result = _analyze_and_store(payload)
            message = {
                "tx_id": result["tx_id"],
                "user_id": payload["user_id"],
                "amount": payload["amount"],
                "time": datetime.utcnow().strftime("%H:%M:%S"),
                "location": payload["location"],
                "device_id": payload["device_id"],
                "risk_score": result["risk_score"],
                "risk_level": result["risk_level"],
                "fraud_probability": result["fraud_probability"],
                "reasons": result["reasons"],
            }
            await websocket.send_text(json.dumps(message))
            await asyncio.sleep(random.uniform(2.5, 4.5))
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
