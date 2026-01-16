# Case – Real-Time Transaction Monitoring and Alerting

## Running the Project with Docker

This project is fully containerized and can be run locally using Docker and Docker Compose. The stack includes a PostgreSQL database, one API for transaction ingestion, and one API for receiving Grafana alert webhooks.

### Prerequisites

Before starting, make sure the following tools are installed:

- Docker  
- Docker Compose  

You can verify the installation with:

```bash
docker --version
docker compose version
```

### Starting the Environment

From the root directory of the project (where `docker-compose.yml` is located), run:

```bash
docker compose up -d
```

This command will:
- start a PostgreSQL container
- start the transaction ingestion API
- start the Grafana alert webhook API
- expose the required ports for local access

Containers will run in detached mode (`-d`).

### Verifying the Services

After the containers are up, verify that the ingestion API is healthy:

```bash
curl http://localhost:3002/health
```

A healthy response indicates that the API is running and connected to the database.

You can also check running containers with:

```bash
docker compose ps
```

### Stopping the Environment

To stop all services:

```bash
docker compose down
```

This will stop and remove the containers while preserving volumes (unless explicitly removed).

### Notes

To login in Grafana use the credentials user: admin | password: admin

All configuration values (database credentials, ports, webhook token) are provided via environment variables defined in `docker-compose.yml`.

The project is designed to run entirely locally but can be adapted for cloud or CI environments with minimal changes.

---

## Architecture Overview

The system consists of a PostgreSQL database, two FastAPI services, and Grafana.

Transaction events are sent to an ingestion API, which stores and aggregates data in PostgreSQL. SQL views compute transaction rates and detect abnormal behavior based on recent history.

Grafana is used to visualize the data and evaluate alert rules. When an alert is triggered, Grafana sends a webhook to a second API, which stores the alert information for auditing and analysis.

---

## API Endpoints

The system exposes two HTTP endpoints.

### Transaction Ingestion API

- **POST `http://localhost:3002/ingest/transaction`**

Receives transaction events and stores them in PostgreSQL for aggregation and monitoring.

### Grafana Alert Webhook

- **POST `http://localhost:3001/grafana/webhook`**

Receives alert notifications from Grafana and persists alert metadata for auditing and analysis.

---

## Database and SQL Views

The system uses PostgreSQL as the source of truth for monitoring and alerting.

### Core Tables

- **`transactions`**: stores aggregated transaction counts by timestamp and status.
- **`transactions_auth_codes`**: stores aggregated counts by timestamp and authorization code (used for operational diagnosis in Grafana).

### Monitoring Views

- **`v_tx_minute`**: aggregates transactions into per-minute totals per status and computes outcome rates (`denied_rate`, `failed_rate`, `reversed_rate`).
- **`v_tx_anomaly`**: computes rolling baseline statistics over recent history and outputs anomaly flags (`denied_above_normal`, `failed_above_normal`, `reversed_above_normal`).

---

## Dashboards and Alerting

Grafana is used for real-time visualization and alert evaluation.

### Dashboards

Dashboards provide visibility into transaction behavior and system health, including:
- total transactions per minute
- denied, failed, and reversed rates over time
- recent alerts table 
- authorization code breakdowns for operational diagnosis

### Alerting

Grafana alert rules are evaluated on top of the anomaly signals exposed by the database views.  
Alerts are triggered when abnormal transaction behavior is detected and are sent to the webhook API for persistence and auditing.

---

## Testing and Validation

The system was validated using synthetic transaction traffic.

Because anomaly detection is based on a rolling baseline of 30 minutes, it is recommended to first generate at least 30 minutes of normal traffic to establish a stable baseline. After this baseline period is in place, controlled spikes are injected for a specific outcome (denied, failed, or reversed) to verify anomaly detection.

Validation confirms that:
- transaction ingestion and database writes function correctly
- SQL aggregations and anomaly views produce expected signals
- Grafana dashboards update in near real time
- alert rules trigger under abnormal conditions
- webhook notifications are received and persisted correctly

This approach ensures the end-to-end monitoring and alerting flow behaves as expected.
