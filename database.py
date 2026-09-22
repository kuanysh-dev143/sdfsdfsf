"""
database.py
------------
SQLite persistence layer for the AI Fraud Detection System (demo/sandbox).

IMPORTANT (security):
This module intentionally NEVER stores real payment secrets.
Only a masked `card_token` (e.g. "CARD-4821") is persisted - never a full PAN,
CVV, PIN, or any other sensitive payment credential. All data used here is
synthetic / test data generated for demonstration purposes only.
"""

import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, Iterator

DB_PATH = os.path.join(os.path.dirname(__file__), "fraud_detection.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Enforce foreign keys + reasonable timeouts to avoid "database is locked" issues
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables if they do not exist yet."""
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id             TEXT PRIMARY KEY,
                avg_amount          REAL DEFAULT 15000,
                typical_start_hour  INTEGER DEFAULT 8,
                typical_end_hour    INTEGER DEFAULT 23,
                typical_location    TEXT DEFAULT 'Astana',
                typical_device      TEXT DEFAULT 'Device-A',
                avg_tx_per_day      REAL DEFAULT 3,
                created_at          TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS devices (
                device_id   TEXT PRIMARY KEY,
                user_id     TEXT,
                first_seen  TEXT DEFAULT (datetime('now')),
                is_trusted  INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_id                   TEXT UNIQUE NOT NULL,
                user_id                 TEXT NOT NULL,
                card_token              TEXT,
                amount                  REAL NOT NULL,
                tx_time                 TEXT NOT NULL,
                location                TEXT,
                device_id               TEXT,
                ip_node                 TEXT,
                merchant                TEXT,
                previous_tx_count       INTEGER DEFAULT 0,
                tx_last_10min           INTEGER DEFAULT 0,
                is_new_device           INTEGER DEFAULT 0,
                distance_from_prev_km   REAL DEFAULT 0,
                risk_score              REAL,
                risk_level              TEXT,
                fraud_probability       REAL,
                reasons                 TEXT,
                status                  TEXT,
                created_at              TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS risk_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_id       TEXT NOT NULL,
                reason      TEXT NOT NULL,
                severity    TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (tx_id) REFERENCES transactions(tx_id)
            );

            CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions(user_id);
            CREATE INDEX IF NOT EXISTS idx_tx_time ON transactions(tx_time);
            CREATE INDEX IF NOT EXISTS idx_tx_risk ON transactions(risk_level);
            """
        )


def seed_demo_data() -> None:
    """Populate the DB with a handful of demo users/devices/transactions
    the first time it runs, so the dashboard is not empty on first load."""
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM transactions").fetchone()["c"]
        if count > 0:
            return

    from fraud_model import get_model, build_feature_row, score_transaction
    import random

    random.seed(42)
    demo_users = [
        {"user_id": "USER-1001", "avg_amount": 18000, "typical_location": "Astana", "typical_device": "Device-A21"},
        {"user_id": "USER-1024", "avg_amount": 25000, "typical_location": "Almaty", "typical_device": "Device-B07"},
        {"user_id": "USER-1077", "avg_amount": 9000, "typical_location": "Shymkent", "typical_device": "Device-C14"},
    ]
    with get_conn() as conn:
        for u in demo_users:
            conn.execute(
                """INSERT OR IGNORE INTO users (user_id, avg_amount, typical_location, typical_device)
                   VALUES (?, ?, ?, ?)""",
                (u["user_id"], u["avg_amount"], u["typical_location"], u["typical_device"]),
            )
            conn.execute(
                """INSERT OR IGNORE INTO devices (device_id, user_id, is_trusted)
                   VALUES (?, ?, 1)""",
                (u["typical_device"], u["user_id"]),
            )

    model = get_model()
    locations = ["Astana", "Almaty", "Shymkent", "Karaganda"]
    for i in range(18):
        user = random.choice(demo_users)
        is_suspicious = random.random() < 0.25
        amount = (
            random.uniform(user["avg_amount"] * 8, user["avg_amount"] * 25)
            if is_suspicious
            else random.uniform(user["avg_amount"] * 0.3, user["avg_amount"] * 1.8)
        )
        hour = random.choice([2, 3, 4]) if is_suspicious else random.randint(8, 22)
        device = "New-Device-" + str(random.randint(100, 999)) if is_suspicious else user["typical_device"]
        location = random.choice(locations) if is_suspicious else user["typical_location"]
        tx_payload = {
            "user_id": user["user_id"],
            "amount": round(amount, 2),
            "tx_time": f"2026-09-{random.randint(15, 22):02d}T{hour:02d}:{random.randint(0,59):02d}:00",
            "location": location,
            "device_id": device,
            "previous_tx_count": random.randint(0, 40),
            "tx_last_10min": random.randint(3, 9) if is_suspicious else random.randint(0, 1),
            "is_new_device": device != user["typical_device"],
            "distance_from_prev_km": random.uniform(300, 1200) if is_suspicious else random.uniform(0, 15),
        }
        row, profile = build_feature_row(tx_payload)
        result = score_transaction(model, row, profile, tx_payload)
        insert_transaction(tx_payload, result, tx_id=f"TX-{1000+i}")


def get_or_create_user(user_id: str) -> sqlite3.Row:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row


def register_device(device_id: str, user_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO devices (device_id, user_id, is_trusted) VALUES (?, ?, 0)",
            (device_id, user_id),
        )


def insert_transaction(payload: dict, result: dict, tx_id: Optional[str] = None) -> str:
    if tx_id is None:
        tx_id = f"TX-{int(datetime.utcnow().timestamp() * 1000) % 1_000_000}"

    card_token = payload.get("card_token") or f"CARD-{abs(hash(payload['user_id'])) % 9000 + 1000}"
    ip_node = payload.get("ip_node") or f"IP-NODE-{abs(hash(payload.get('device_id',''))) % 90 + 10}"
    merchant = payload.get("merchant") or f"MERCHANT-{abs(hash(tx_id)) % 90 + 10}"

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO transactions
               (tx_id, user_id, card_token, amount, tx_time, location, device_id, ip_node, merchant,
                previous_tx_count, tx_last_10min, is_new_device, distance_from_prev_km,
                risk_score, risk_level, fraud_probability, reasons, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                tx_id,
                payload["user_id"],
                card_token,
                payload["amount"],
                payload["tx_time"],
                payload.get("location"),
                payload.get("device_id"),
                ip_node,
                merchant,
                payload.get("previous_tx_count", 0),
                payload.get("tx_last_10min", 0),
                int(bool(payload.get("is_new_device", False))),
                payload.get("distance_from_prev_km", 0),
                result["risk_score"],
                result["risk_level"],
                result["fraud_probability"],
                "||".join(result["reasons"]),
                result["risk_level"],
            ),
        )
        for reason in result["reasons"]:
            conn.execute(
                "INSERT INTO risk_events (tx_id, reason, severity) VALUES (?, ?, ?)",
                (tx_id, reason, result["risk_level"]),
            )
    return tx_id


def list_transactions(limit: int = 100) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM transactions ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_statistics() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"]
        suspicious = conn.execute(
            "SELECT COUNT(*) c FROM transactions WHERE risk_level IN ('MEDIUM','HIGH','CRITICAL')"
        ).fetchone()["c"]
        high_risk = conn.execute(
            "SELECT COUNT(*) c FROM transactions WHERE risk_level IN ('HIGH','CRITICAL')"
        ).fetchone()["c"]
        avg_score = conn.execute("SELECT AVG(risk_score) a FROM transactions").fetchone()["a"] or 0
        by_level = conn.execute(
            "SELECT risk_level, COUNT(*) c FROM transactions GROUP BY risk_level"
        ).fetchall()

    detection_rate = round((suspicious / total) * 100, 1) if total else 0.0
    return {
        "total_transactions": total,
        "suspicious_transactions": suspicious,
        "high_risk_transactions": high_risk,
        "detection_rate": detection_rate,
        "average_risk_score": round(avg_score, 1),
        "by_level": {r["risk_level"]: r["c"] for r in by_level},
    }


def get_network_graph() -> dict:
    """Builds a simple node/edge graph: User -> Card -> Device -> IP -> Merchant."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT user_id, card_token, device_id, ip_node, merchant, risk_level
               FROM transactions ORDER BY created_at DESC LIMIT 40"""
        ).fetchall()

    nodes = {}
    edges = []

    def add_node(node_id, node_type, risk="LOW"):
        if node_id not in nodes:
            nodes[node_id] = {"id": node_id, "type": node_type, "risk": risk}
        elif risk in ("HIGH", "CRITICAL"):
            nodes[node_id]["risk"] = risk

    for r in rows:
        risk = r["risk_level"] or "LOW"
        add_node(r["user_id"], "user", risk)
        add_node(r["card_token"], "card", risk)
        add_node(r["device_id"], "device", risk)
        add_node(r["ip_node"], "ip", risk)
        add_node(r["merchant"], "merchant", risk)
        edges.append({"source": r["user_id"], "target": r["card_token"]})
        edges.append({"source": r["card_token"], "target": r["device_id"]})
        edges.append({"source": r["device_id"], "target": r["ip_node"]})
        edges.append({"source": r["ip_node"], "target": r["merchant"]})

    # de-duplicate edges
    seen = set()
    unique_edges = []
    for e in edges:
        key = (e["source"], e["target"])
        if key not in seen:
            seen.add(key)
            unique_edges.append(e)

    return {"nodes": list(nodes.values()), "edges": unique_edges}
