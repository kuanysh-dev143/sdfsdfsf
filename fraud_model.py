"""
fraud_model.py
---------------
Real ML pipeline for the AI Fraud Detection System (demo/sandbox).

Pipeline:
  1. Generate synthetic "normal" and "suspicious" transaction patterns.
  2. Engineer behavioral features (deviation from user's typical behavior).
  3. Train a RandomForestClassifier (supervised, probability of fraud) and
     an IsolationForest (unsupervised, anomaly detection for unknown patterns).
  4. Persist both models to disk with joblib.
  5. Combine both signals into a single 0-100 risk score with
     human-readable, rule-based explanations (Explainable AI layer).

No real financial data is used anywhere in this file - everything here is
synthetically generated or supplied by the demo frontend as test data.
"""

import os
import random
from datetime import datetime
from typing import Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, IsolationForest
from sklearn.model_selection import train_test_split
import joblib

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
os.makedirs(MODEL_DIR, exist_ok=True)
RF_PATH = os.path.join(MODEL_DIR, "rf_classifier.joblib")
IF_PATH = os.path.join(MODEL_DIR, "isolation_forest.joblib")

FEATURE_COLUMNS = [
    "amount_ratio",          # amount / user's average amount
    "hour_deviation",        # how far outside typical hours (0-12)
    "is_new_device",         # 0 / 1
    "is_new_location",       # 0 / 1
    "distance_from_prev_km",
    "tx_last_10min",
    "previous_tx_count_low", # 1 if account has very little history (0-3 tx)
    "amount_log",
]

RISK_THRESHOLDS = [
    (85, "CRITICAL"),
    (65, "HIGH"),
    (35, "MEDIUM"),
    (0, "LOW"),
]


# ---------------------------------------------------------------------------
# 1. Synthetic data generation
# ---------------------------------------------------------------------------

def _generate_synthetic_dataset(n_samples: int = 6000, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []

    n_fraud = int(n_samples * 0.22)
    n_normal = n_samples - n_fraud

    # Normal transactions: close to a user's typical behavior
    for _ in range(n_normal):
        avg_amount = rng.uniform(5000, 60000)
        amount = max(500, rng.normal(avg_amount, avg_amount * 0.35))
        hour_dev = abs(rng.normal(0, 1.5))
        is_new_device = rng.random() < 0.03
        is_new_location = rng.random() < 0.04
        distance = rng.exponential(3) if not is_new_location else rng.uniform(0, 50)
        tx_last_10min = max(0, int(rng.normal(0.4, 0.6)))
        prev_tx_count = rng.integers(4, 500)

        rows.append(_make_row(amount, avg_amount, hour_dev, is_new_device,
                               is_new_location, distance, tx_last_10min, prev_tx_count, label=0))

    # Fraudulent / suspicious transactions: deliberately deviate
    for _ in range(n_fraud):
        avg_amount = rng.uniform(5000, 60000)
        spike = rng.uniform(6, 40)
        amount = avg_amount * spike
        hour_dev = abs(rng.normal(7, 3))
        is_new_device = rng.random() < 0.75
        is_new_location = rng.random() < 0.7
        distance = rng.uniform(200, 3000) if is_new_location else rng.uniform(0, 30)
        tx_last_10min = max(0, int(rng.normal(6, 3)))
        prev_tx_count = rng.integers(0, 15)

        rows.append(_make_row(amount, avg_amount, hour_dev, is_new_device,
                               is_new_location, distance, tx_last_10min, prev_tx_count, label=1))

    df = pd.DataFrame(rows)
    return df.sample(frac=1, random_state=seed).reset_index(drop=True)


def _make_row(amount, avg_amount, hour_dev, is_new_device, is_new_location,
              distance, tx_last_10min, prev_tx_count, label) -> dict:
    return {
        "amount_ratio": amount / max(avg_amount, 1),
        "hour_deviation": min(hour_dev, 12),
        "is_new_device": int(is_new_device),
        "is_new_location": int(is_new_location),
        "distance_from_prev_km": distance,
        "tx_last_10min": tx_last_10min,
        "previous_tx_count_low": int(prev_tx_count <= 3),
        "amount_log": np.log1p(amount),
        "label": label,
    }


# ---------------------------------------------------------------------------
# 2. Training
# ---------------------------------------------------------------------------

def train_models() -> Tuple[RandomForestClassifier, IsolationForest]:
    df = _generate_synthetic_dataset()
    X = df[FEATURE_COLUMNS]
    y = df["label"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=7, stratify=y
    )

    rf = RandomForestClassifier(
        n_estimators=200,
        max_depth=10,
        min_samples_leaf=3,
        class_weight="balanced",
        random_state=7,
        n_jobs=-1,
    )
    rf.fit(X_train, y_train)

    # Isolation Forest trained mainly on "normal" data to catch novel anomalies
    normal_only = X_train[y_train == 0]
    iso = IsolationForest(
        n_estimators=200,
        contamination=0.1,
        random_state=7,
    )
    iso.fit(normal_only)

    joblib.dump(rf, RF_PATH)
    joblib.dump(iso, IF_PATH)
    return rf, iso


def get_model() -> Tuple[RandomForestClassifier, IsolationForest]:
    """Load models from disk, training them the first time this is called."""
    if os.path.exists(RF_PATH) and os.path.exists(IF_PATH):
        rf = joblib.load(RF_PATH)
        iso = joblib.load(IF_PATH)
        return rf, iso
    return train_models()


# ---------------------------------------------------------------------------
# 3. Feature engineering for a live transaction
# ---------------------------------------------------------------------------

def build_feature_row(payload: dict) -> Tuple[pd.DataFrame, dict]:
    """Turns a raw transaction payload into a model-ready feature row,
    using the user's stored behavioral profile for comparison."""
    import database  # local import to avoid circular import at module load

    user_id = payload["user_id"]
    profile_row = database.get_or_create_user(user_id)
    profile = dict(profile_row)

    avg_amount = profile.get("avg_amount") or 15000
    typical_start = profile.get("typical_start_hour", 8)
    typical_end = profile.get("typical_end_hour", 23)
    typical_location = profile.get("typical_location", "Astana")
    typical_device = profile.get("typical_device", "Device-A")

    amount = float(payload["amount"])
    tx_time_raw = payload.get("tx_time") or datetime.utcnow().isoformat()
    try:
        hour = datetime.fromisoformat(tx_time_raw).hour
    except ValueError:
        hour = datetime.utcnow().hour

    if typical_start <= hour <= typical_end:
        hour_dev = 0
    else:
        hour_dev = min(min(abs(hour - typical_start), abs(hour - typical_end)), 12)

    device_id = payload.get("device_id", "")
    location = payload.get("location", "")
    is_new_device = bool(payload.get("is_new_device", device_id != typical_device))
    is_new_location = location != typical_location
    distance = float(payload.get("distance_from_prev_km", 0) or 0)
    tx_last_10min = int(payload.get("tx_last_10min", 0) or 0)
    prev_tx_count = int(payload.get("previous_tx_count", 0) or 0)

    row = {
        "amount_ratio": amount / max(avg_amount, 1),
        "hour_deviation": hour_dev,
        "is_new_device": int(is_new_device),
        "is_new_location": int(is_new_location),
        "distance_from_prev_km": distance,
        "tx_last_10min": tx_last_10min,
        "previous_tx_count_low": int(prev_tx_count <= 3),
        "amount_log": np.log1p(amount),
    }
    return pd.DataFrame([row], columns=FEATURE_COLUMNS), profile


# ---------------------------------------------------------------------------
# 4. Scoring + Explainable AI
# ---------------------------------------------------------------------------

def _risk_level_for(score: float) -> str:
    for threshold, level in RISK_THRESHOLDS:
        if score >= threshold:
            return level
    return "LOW"


def _build_reasons(row: dict, amount_ratio: float, iso_anomaly: bool) -> list:
    reasons = []
    if amount_ratio >= 8:
        reasons.append("Transaction amount is significantly higher than user's normal behavior.")
    elif amount_ratio >= 3:
        reasons.append("Transaction amount is noticeably above the user's typical spending.")

    if row["is_new_device"]:
        reasons.append("New device detected.")
    if row["is_new_location"]:
        reasons.append("Location differs significantly from previous activity.")
    if row["hour_deviation"] >= 3:
        reasons.append("Unusual transaction time.")
    if row["tx_last_10min"] >= 4:
        reasons.append("Multiple transactions within a short period.")
    if row["distance_from_prev_km"] >= 300:
        reasons.append("Transaction location is implausibly far from the previous one.")
    if row["previous_tx_count_low"]:
        reasons.append("Account has very little transaction history.")
    if iso_anomaly:
        reasons.append("Behavioral pattern flagged as a statistical anomaly by the anomaly-detection model.")

    if not reasons:
        reasons.append("Transaction is consistent with the user's normal behavioral profile.")
    return reasons


def score_transaction(models, feature_row: pd.DataFrame, profile: dict, payload: dict) -> dict:
    rf, iso = models
    row = feature_row.iloc[0].to_dict()

    fraud_probability = float(rf.predict_proba(feature_row)[0][1])

    # IsolationForest: -1 = anomaly, 1 = normal. decision_function: lower = more anomalous.
    iso_pred = iso.predict(feature_row)[0]
    iso_anomaly = iso_pred == -1
    iso_score_raw = iso.decision_function(feature_row)[0]  # roughly in [-0.5, 0.5]
    iso_component = np.clip((0.2 - iso_score_raw) / 0.4, 0, 1)  # normalize to 0-1, higher = more anomalous

    # Blend supervised probability (70%) with anomaly signal (30%)
    combined = 0.7 * fraud_probability + 0.3 * iso_component
    risk_score = round(float(np.clip(combined * 100, 0, 100)), 1)
    risk_level = _risk_level_for(risk_score)

    reasons = _build_reasons(row, row["amount_ratio"], iso_anomaly)

    return {
        "risk_score": risk_score,
        "risk_level": risk_level,
        "fraud_probability": round(fraud_probability, 4),
        "anomaly_detected": bool(iso_anomaly),
        "reasons": reasons,
    }


if __name__ == "__main__":
    print("Training fraud detection models on synthetic data...")
    train_models()
    print(f"Saved: {RF_PATH}")
    print(f"Saved: {IF_PATH}")
