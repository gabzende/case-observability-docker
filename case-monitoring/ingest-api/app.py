import os
import time
from datetime import datetime, timezone
from contextlib import contextmanager

import psycopg2
from psycopg2 import pool
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

app = FastAPI(title="Transaction Ingestion API")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "case_study")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "admin123")

# Timeouts
STATEMENT_TIMEOUT_MS = int(os.getenv("STATEMENT_TIMEOUT_MS", "5000"))  # 5s
CONNECT_TIMEOUT_S = int(os.getenv("CONNECT_TIMEOUT_S", "5"))          # 5s

# Pool sizing
POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

# Startup retry settings (new)
DB_STARTUP_RETRY_SECONDS = int(os.getenv("DB_STARTUP_RETRY_SECONDS", "60"))
DB_STARTUP_RETRY_INTERVAL = float(os.getenv("DB_STARTUP_RETRY_INTERVAL", "2"))

ALLOWED_STATUSES = {"approved", "denied", "failed", "reversed", "backend_reversed"}

# We'll create the pool on startup
db_pool: pool.ThreadedConnectionPool | None = None


def _make_pool() -> pool.ThreadedConnectionPool:
    """
    Creates a thread-safe psycopg2 connection pool with sane timeouts.
    """
    return pool.ThreadedConnectionPool(
        POOL_MIN,
        POOL_MAX,
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        connect_timeout=CONNECT_TIMEOUT_S,
        # statement_timeout protects you from hanging queries/locks
        options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
        # Optional TCP keepalives (helps Windows/network hiccups)
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )


def _init_pool_with_retry() -> pool.ThreadedConnectionPool:
    """
    Try to create the pool for up to DB_STARTUP_RETRY_SECONDS.
    This prevents the container from exiting when Postgres isn't ready yet.
    """
    deadline = time.time() + DB_STARTUP_RETRY_SECONDS
    last_err: Exception | None = None

    while time.time() < deadline:
        try:
            p = _make_pool()
            # quick sanity check: get a conn and run SELECT 1
            conn = p.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            finally:
                p.putconn(conn)
            return p
        except Exception as e:
            last_err = e
            time.sleep(DB_STARTUP_RETRY_INTERVAL)

    raise RuntimeError(f"DB not reachable after retries: {last_err}")


@app.on_event("startup")
def startup():
    global db_pool
    if db_pool is None:
        db_pool = _init_pool_with_retry()


@app.on_event("shutdown")
def shutdown():
    global db_pool
    if db_pool is not None:
        db_pool.closeall()
        db_pool = None


@contextmanager
def get_db_connection():
    """
    Borrow a connection from the pool safely.

    - Ensures connection is usable
    - Ensures we never return a connection with an open transaction
    """
    global db_pool
    if db_pool is None:
        # Safety net: if the app starts handling requests before DB is ready,
        # retry briefly instead of crashing immediately.
        db_pool = _init_pool_with_retry()

    conn = db_pool.getconn()
    try:
        # Ensure non-autocommit (we control commit/rollback)
        conn.autocommit = False

        # Defensive cleanup: if anything was left open
        try:
            conn.rollback()
        except Exception:
            pass

        # Pre-ping: if connection went stale, recreate it
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        except Exception:
            # Drop and recreate this connection (standalone)
            try:
                conn.close()
            except Exception:
                pass
            conn = psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASS,
                connect_timeout=CONNECT_TIMEOUT_S,
                options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5,
            )
            conn.autocommit = False

        yield conn
        conn.commit()

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise

    finally:
        # Make sure we return it clean
        try:
            conn.rollback()
        except Exception:
            pass
        db_pool.putconn(conn)


class Transaction(BaseModel):
    ts: str
    status: str
    auth_code: int | None = Field(default=None)

    @field_validator("auth_code")
    @classmethod
    def validate_auth_code(cls, v):
        if v is None:
            return v
        if v < 0 or v > 9999:
            raise ValueError("auth_code must be between 0 and 9999")
        return v


def parse_iso_ts(ts_str: str) -> datetime:
    if ts_str.endswith("Z"):
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(ts_str)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


# NOTE: kept (unused) for compatibility; you can delete if you want
def floor_to_minute_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


@app.post("/ingest/transaction")
def ingest_transaction(tx: Transaction):
    try:
        event_ts = parse_iso_ts(tx.ts)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ISO-8601 timestamp")

    if tx.status not in ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {tx.status}")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # ✅ EVENT-BASED: one row per transaction (no bucketing, no upsert)
                cur.execute(
                    """
                    INSERT INTO public.transactions (ts, status, count)
                    VALUES (%s, %s, 1)
                    """,
                    (event_ts, tx.status),
                )

                # ✅ EVENT-BASED: one row per auth_code event (count=1)
                if tx.auth_code is not None:
                    cur.execute(
                        """
                        INSERT INTO public.transactions_auth_codes (ts, auth_code, count)
                        VALUES (%s, %s, 1)
                        """,
                        (event_ts, tx.auth_code),
                    )

        return {
            "status": "ok",
            "event_ts": event_ts.isoformat(),
            "status_sent": tx.status,
            "auth_code": tx.auth_code,
        }

    except psycopg2.Error as e:
        msg = (e.pgerror or str(e)).strip()
        raise HTTPException(status_code=500, detail=f"Database error: {msg}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")


@app.get("/health")
def health_check():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unhealthy: {e}")
