SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

CREATE SCHEMA IF NOT EXISTS public;
COMMENT ON SCHEMA public IS 'standard public schema';
SET default_table_access_method = heap;

CREATE TABLE public.alerts (
    id integer NOT NULL,
    ts timestamptz NOT NULL,
    alert_type text NOT NULL,
    severity_score double precision NOT NULL,
    details jsonb DEFAULT '{}'::jsonb,
    created_at timestamptz DEFAULT now()
);

CREATE SEQUENCE public.alerts_id_seq
    START WITH 1
    INCREMENT BY 1;

ALTER SEQUENCE public.alerts_id_seq OWNED BY public.alerts.id;

ALTER TABLE public.alerts
    ALTER COLUMN id SET DEFAULT nextval('public.alerts_id_seq');

CREATE TABLE public.checkout_1 (
    "time" text,
    today integer,
    yesterday integer,
    same_day_last_week integer,
    avg_last_week numeric(10,2),
    avg_last_month numeric(10,2)
);

CREATE TABLE public.checkout_2 (
    "time" text,
    today integer,
    yesterday integer,
    same_day_last_week integer,
    avg_last_week numeric(10,2),
    avg_last_month numeric(10,2)
);

CREATE TABLE public.transaction_alerts (
    id bigint NOT NULL,
    created_at timestamptz DEFAULT now() NOT NULL,
    bucket_ts timestamptz NOT NULL,
    state text NOT NULL CHECK (state IN ('firing','resolved')),
    denied_above_normal boolean DEFAULT false NOT NULL,
    failed_above_normal boolean DEFAULT false NOT NULL,
    reversed_above_normal boolean DEFAULT false NOT NULL,
    reasons text[] DEFAULT '{}' NOT NULL,
    severity_score double precision DEFAULT 0 NOT NULL,
    total integer DEFAULT 0 NOT NULL,
    approved integer DEFAULT 0 NOT NULL,
    denied integer DEFAULT 0 NOT NULL,
    failed integer DEFAULT 0 NOT NULL,
    reversed integer DEFAULT 0 NOT NULL,
    denied_rate double precision DEFAULT 0 NOT NULL,
    failed_rate double precision DEFAULT 0 NOT NULL,
    reversed_rate double precision DEFAULT 0 NOT NULL,
    denied_mean double precision,
    denied_std double precision,
    failed_mean double precision,
    failed_std double precision,
    reversed_mean double precision,
    reversed_std double precision,
    raw_payload jsonb
);

CREATE SEQUENCE public.transaction_alerts_id_seq START WITH 1;
ALTER SEQUENCE public.transaction_alerts_id_seq OWNED BY public.transaction_alerts.id;
ALTER TABLE public.transaction_alerts
    ALTER COLUMN id SET DEFAULT nextval('public.transaction_alerts_id_seq');

CREATE TABLE public.transactions (
    ts timestamptz NOT NULL,
    status text NOT NULL,
    count integer NOT NULL
);

CREATE TABLE public.transactions_auth_codes (
    ts timestamptz NOT NULL,
    auth_code integer NOT NULL,
    count integer NOT NULL
);

CREATE VIEW public.v_tx_minute AS
SELECT
    ts,
    sum(count) AS total,
    sum(count) FILTER (WHERE status = 'approved') AS approved,
    sum(count) FILTER (WHERE status = 'denied') AS denied,
    sum(count) FILTER (WHERE status = 'failed') AS failed,
    sum(count) FILTER (
        WHERE status IN ('reversed','backend_reversed')
    ) AS reversed,
    sum(count) FILTER (WHERE status='denied')::float / NULLIF(sum(count),0) AS denied_rate,
    sum(count) FILTER (WHERE status='failed')::float / NULLIF(sum(count),0) AS failed_rate,
    sum(count) FILTER (
        WHERE status IN ('reversed','backend_reversed')
    )::float / NULLIF(sum(count),0) AS reversed_rate
FROM public.transactions
GROUP BY ts;

CREATE VIEW public.v_tx_anomaly AS
WITH base AS (
    SELECT
        v.*,
        avg(denied_rate)   OVER w AS denied_mean,
        stddev_samp(denied_rate) OVER w AS denied_std,
        avg(failed_rate)   OVER w AS failed_mean,
        stddev_samp(failed_rate) OVER w AS failed_std,
        avg(reversed_rate) OVER w AS reversed_mean,
        stddev_samp(reversed_rate) OVER w AS reversed_std
    FROM public.v_tx_minute v
    WINDOW w AS (ORDER BY ts ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING)
)
SELECT *,
    (total >= 30 AND denied_std   IS NOT NULL AND denied_rate   > denied_mean   + 3*denied_std)   AS denied_above_normal,
    (total >= 30 AND failed_std   IS NOT NULL AND failed_rate   > failed_mean   + 3*failed_std)   AS failed_above_normal,
    (total >= 30 AND reversed_std IS NOT NULL AND reversed_rate > reversed_mean + 3*reversed_std) AS reversed_above_normal
FROM base;

ALTER TABLE public.alerts ADD CONSTRAINT alerts_pkey PRIMARY KEY (id);
ALTER TABLE public.alerts ADD CONSTRAINT alerts_unique_ts_type UNIQUE (ts, alert_type);
ALTER TABLE public.transaction_alerts ADD CONSTRAINT transaction_alerts_pkey PRIMARY KEY (id);
ALTER TABLE public.transactions ADD CONSTRAINT transactions_pkey PRIMARY KEY (ts, status);
ALTER TABLE public.transactions_auth_codes ADD CONSTRAINT transactions_auth_codes_pkey PRIMARY KEY (ts, auth_code);

CREATE INDEX idx_alerts_ts ON public.alerts (ts DESC);
CREATE INDEX idx_transactions_auth_codes_ts ON public.transactions_auth_codes (ts);
CREATE INDEX ix_transaction_alerts_bucket_ts_desc ON public.transaction_alerts (bucket_ts DESC);
CREATE INDEX ix_transaction_alerts_created_at_desc ON public.transaction_alerts (created_at DESC);
CREATE UNIQUE INDEX ux_transaction_alerts_bucket_state ON public.transaction_alerts (bucket_ts, state);

-- Importar dados iniciais (seed)
\i /docker-entrypoint-initdb.d/data/checkout_1.sql
\i /docker-entrypoint-initdb.d/data/checkout_2.sql
\i /docker-entrypoint-initdb.d/data/transactions.sql
\i /docker-entrypoint-initdb.d/data/transactions_auth_codes.sql
\i /docker-entrypoint-initdb.d/data/transaction_alerts.sql

