FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY audit_ledger.py client_compliance_probe.py client_compliance_status_cli.py core.py dream_maintenance.py dream_maintenance_cli.py hook_heartbeat.py ingestion.py ingestion_llm.py ingestion_status_cli.py kontext_shadow_compare.py kontext_write_audit.py lifecycle.py lifecycle_cli.py memory_snapshot.py memory_snapshot_cli.py metadata_enrichment.py metadata_enrichment_cli.py monitoring.py project_observations.py project_observations_cli.py project_retrieval.py retrieval.py retrieval_eval.py retrieval_eval_cases.sample.json retrieval_eval_cases.v1.13.json retrieval_telemetry.py server_status.py stale_config_guard.py usage_report.py usage_report_cli.py server.py ./
COPY client_hooks ./client_hooks

CMD python server.py
