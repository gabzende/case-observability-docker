import os
import json
from datetime import datetime, timezone
import psycopg2
from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI(title="Grafana Alert Webhook")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "case_study")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "admin123")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "super-secret-token")


def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )


def parse_event_ts(payload: dict) -> datetime:
    alerts = payload.get("alerts", [])
    if alerts:
        starts_at = alerts[0].get("startsAt") or alerts[0].get("starts_at")
        if starts_at:
            try:
                return datetime.fromisoformat(starts_at.replace("Z", "+00:00")).astimezone(
                    timezone.utc
                )
            except Exception:
                pass
    return datetime.now(timezone.utc)


def floor_to_minute_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


@app.post("/grafana/webhook")
async def grafana_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ", 1)[1].strip()
    if token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    payload = await request.json()

    state = (payload.get("status") or "").lower().strip()
    if state not in ("firing", "resolved"):
        state = "firing"

    event_ts = parse_event_ts(payload)
    bucket_ts = floor_to_minute_utc(event_ts)

    raw_payload = json.dumps(payload)

    try:
        conn = get_conn()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      COALESCE(denied_above_normal, false),
                      COALESCE(failed_above_normal, false),
                      COALESCE(reversed_above_normal, false),
                      COALESCE(total, 0),
                      COALESCE(approved, 0),
                      COALESCE(denied, 0),
                      COALESCE(failed, 0),
                      COALESCE(reversed, 0),
                      COALESCE(denied_rate, 0),
                      COALESCE(failed_rate, 0),
                      COALESCE(reversed_rate, 0),
                      denied_mean, denied_std,
                      failed_mean, failed_std,
                      reversed_mean, reversed_std
                    FROM public.v_tx_anomaly
                    WHERE ts = %s
                    """,
                    (bucket_ts,),
                )
                row = cur.fetchone()

                if row:
                    denied_flag, failed_flag, reversed_flag, total, approved, denied, failed, reversed, denied_rate, failed_rate, reversed_rate, denied_mean, denied_std, failed_mean, failed_std, reversed_mean, reversed_std = row
                else:
                    denied_flag = failed_flag = reversed_flag = False
                    total = approved = denied = failed = reversed = 0
                    denied_rate = failed_rate = reversed_rate = 0.0
                    denied_mean = denied_std = failed_mean = failed_std = reversed_mean = reversed_std = None

                reasons = []
                if denied_flag:
                    reasons.append("denied_above_normal")
                if failed_flag:
                    reasons.append("failed_above_normal")
                if reversed_flag:
                    reasons.append("reversed_above_normal")

                severity_score = float(len(reasons))

                cur.execute(
                    """
                    INSERT INTO public.transaction_alerts (
                      bucket_ts, state,
                      denied_above_normal, failed_above_normal, reversed_above_normal,
                      reasons, severity_score,
                      total, approved, denied, failed, reversed,
                      denied_rate, failed_rate, reversed_rate,
                      denied_mean, denied_std, failed_mean, failed_std, reversed_mean, reversed_std,
                      raw_payload
                    )
                    VALUES (
                      %s, %s,
                      %s, %s, %s,
                      %s, %s,
                      %s, %s, %s, %s, %s,
                      %s, %s, %s,
                      %s, %s, %s, %s, %s, %s,
                      %s::jsonb
                    )
                    ON CONFLICT (bucket_ts, state) DO UPDATE
                    SET
                      denied_above_normal = EXCLUDED.denied_above_normal,
                      failed_above_normal = EXCLUDED.failed_above_normal,
                      reversed_above_normal = EXCLUDED.reversed_above_normal,
                      reasons = EXCLUDED.reasons,
                      severity_score = EXCLUDED.severity_score,
                      total = EXCLUDED.total,
                      approved = EXCLUDED.approved,
                      denied = EXCLUDED.denied,
                      failed = EXCLUDED.failed,
                      reversed = EXCLUDED.reversed,
                      denied_rate = EXCLUDED.denied_rate,
                      failed_rate = EXCLUDED.failed_rate,
                      reversed_rate = EXCLUDED.reversed_rate,
                      denied_mean = EXCLUDED.denied_mean,
                      denied_std = EXCLUDED.denied_std,
                      failed_mean = EXCLUDED.failed_mean,
                      failed_std = EXCLUDED.failed_std,
                      reversed_mean = EXCLUDED.reversed_mean,
                      reversed_std = EXCLUDED.reversed_std,
                      raw_payload = EXCLUDED.raw_payload
                    """,
                    (
                        bucket_ts, state,
                        denied_flag, failed_flag, reversed_flag,
                        reasons, severity_score,
                        total, approved, denied, failed, reversed,
                        denied_rate, failed_rate, reversed_rate,
                        denied_mean, denied_std, failed_mean, failed_std, reversed_mean, reversed_std,
                        raw_payload,
                    ),
                )
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB insert failed: {e}")

    return {
        "status": "ok",
        "state": state,
        "event_ts": event_ts.isoformat(),
        "bucket_ts": bucket_ts.isoformat(),
        "reasons": reasons,
        "severity_score": severity_score,
    }