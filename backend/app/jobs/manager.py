"""In-process investigation job manager (SPEC §10.3, §10.5).

Runs the LangGraph workflow as a background task and records real step
transitions so the frontend can poll genuine progress rather than a fabricated
animation.

Known limitation, documented in the README: this is in-process. A restart loses
running jobs, and running more than one worker process gives each its own queue.
The :class:`~app.jobs.manager.InvestigationStore` seam exists so a durable queue
can replace this later without changing the API.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from app.core.errors import (
    AppError,
    InvalidInputError,
    NotFoundError,
    PayloadTooLargeError,
    RateLimitedError,
)
from app.core.ids import new_investigation_id, utc_now
from app.graph.builder import run_investigation
from app.logengine.models import Runtime
from app.models.base import BaseChatModel
from app.models.registry import build_model
from app.store.base import InvestigationRecord, InvestigationStore

logger = logging.getLogger("logsherlock.jobs")

#: Guard against a pathological graph or provider hanging the worker.
GRAPH_WALL_CLOCK_SECONDS = 600


class JobManager:
    """Owns the lifecycle of background investigations."""

    def __init__(
        self,
        *,
        store: InvestigationStore,
        settings: Any,
        model_factory: Any = build_model,
    ) -> None:
        """Initialize with a store, settings, and a model factory."""
        self._store = store
        self._settings = settings
        self._model_factory = model_factory
        self._tasks: dict[str, asyncio.Task[None]] = {}
        # Raw input lives here only for the lifetime of a run and is cleared in
        # the runner's finally block. It is never written to the store unless
        # PERSIST_RAW_LOGS is enabled (SPEC 11.5).
        self._inputs: dict[str, str] = {}
        # Model construction can raise or be expensive; build it once and reuse.
        self._model: BaseChatModel | None = None
        self._model_error: str | None = None

    # -- public API -------------------------------------------------------

    def submit(
        self,
        *,
        content: str,
        runtime: str = "auto",
        context: str | None = None,
        filename: str | None = None,
    ) -> InvestigationRecord:
        """Validate input, create a record, and start the background run.

        All size and format limits are enforced here, synchronously, so the
        caller gets a 4xx immediately rather than a 202 followed by an
        asynchronous failure (SPEC §7.2, §10.9).
        """
        self._validate_content(content)

        self._enforce_concurrency()

        try:
            requested = Runtime(runtime or "auto")
        except ValueError:
            requested = Runtime.AUTO

        record = InvestigationRecord(new_investigation_id())
        record.status = "queued"
        record.runtime = requested.value
        record.created_at = utc_now().isoformat()
        record.warnings = []
        record.processing_steps = []

        if filename:
            record.warnings.append(f"Source file: {filename}")

        # Raw input is held only in memory for the duration of the run and is
        # never persisted unless PERSIST_RAW_LOGS is enabled (SPEC 11.5).
        record.raw_input = content if self._settings.persist_raw_logs else None
        self._inputs[record.id] = content

        self._store.create(record)
        task = asyncio.create_task(self._run(record.id))
        self._tasks[record.id] = task
        task.add_done_callback(lambda _t, rid=record.id: self._tasks.pop(rid, None))

        logger.info("investigation=%s queued (runtime=%s)", record.id, requested.value)
        return record

    def get(self, investigation_id: str) -> InvestigationRecord:
        """Return a record or raise :class:`NotFoundError`."""
        record = self._store.get(investigation_id)
        if record is None:
            raise NotFoundError(f"Investigation {investigation_id} was not found.")
        return record

    def list(self, limit: int, offset: int) -> tuple[list[InvestigationRecord], int]:
        """Return a page of investigations."""
        return self._store.list(limit, offset)

    def delete(self, investigation_id: str) -> None:
        """Delete an investigation and its dependents."""
        task = self._tasks.pop(investigation_id, None)
        if task is not None and not task.done():
            task.cancel()
        self._inputs.pop(investigation_id, None)
        if not self._store.delete(investigation_id):
            raise NotFoundError(f"Investigation {investigation_id} was not found.")

    async def shutdown(self) -> None:
        """Cancel outstanding work on application shutdown."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    # -- internals --------------------------------------------------------

    def _validate_content(self, content: str) -> None:
        """Enforce every input limit synchronously, before anything is queued.

        Mirrors the checks in the graph's ``validate_input`` node. Doing it here
        as well means an oversized or empty payload is rejected with the correct
        status code on the request, not swallowed by a background task.
        """
        if not content or not content.strip():
            raise InvalidInputError("The supplied log content is empty.")

        settings = self._settings

        size = len(content.encode("utf-8"))
        if size > settings.max_upload_bytes:
            raise PayloadTooLargeError(
                f"Log content is {size} bytes, above the {settings.max_upload_mb} MB limit.",
                details={"max_bytes": settings.max_upload_bytes},
            )

        line_count = content.count("\n") + 1
        if line_count > settings.max_log_lines:
            raise PayloadTooLargeError(
                f"Log content has {line_count} lines, above the "
                f"{settings.max_log_lines} line limit.",
                details={"max_lines": settings.max_log_lines},
            )

        longest = max((len(line) for line in content.splitlines()), default=0)
        if longest > settings.max_line_length:
            raise PayloadTooLargeError(
                f"A single log line is {longest} characters, above the "
                f"{settings.max_line_length} character limit.",
                details={"max_line_length": settings.max_line_length},
            )

    def _enforce_concurrency(self) -> None:
        """Reject new work when too many investigations are already running."""
        active = [t for t in self._tasks.values() if not t.done()]
        limit = self._settings.max_concurrent_investigations
        if len(active) >= limit:
            raise RateLimitedError(
                f"{limit} investigation(s) already running. Wait for one to finish and try again.",
                details={"limit": limit},
            )

    def _resolve_model(self) -> tuple[BaseChatModel | None, str | None]:
        """Return the configured model, or the reason it is unavailable."""
        if self._model is not None or self._model_error is not None:
            return self._model, self._model_error
        try:
            self._model = self._model_factory(self._settings)
        except Exception as exc:
            self._model_error = f"Model could not be configured: {exc}"
            logger.warning("investigation model unavailable: %s", self._model_error)
        return self._model, self._model_error

    async def _run(self, investigation_id: str) -> None:
        """Execute the graph for one investigation, recording real progress."""
        record = self._store.get(investigation_id)
        if record is None:
            return

        content = self._inputs.get(investigation_id, "")
        record.status = "running"
        self._store.update(record)

        try:
            model, model_error = self._resolve_model()

            if model is None:
                # No model at all: still run the deterministic half so the user
                # gets a useful report (SPEC 8.5).
                result = await run_investigation(
                    _NoModel(),
                    investigation_id=investigation_id,
                    raw_input=content,
                    settings=self._settings,
                    runtime=record.runtime or "auto",
                )
                result["ai_skipped_reason"] = model_error
                result["status"] = "partial"
            else:
                result = await asyncio.wait_for(
                    run_investigation(
                        model,
                        investigation_id=investigation_id,
                        raw_input=content,
                        settings=self._settings,
                        runtime=record.runtime or "auto",
                    ),
                    timeout=GRAPH_WALL_CLOCK_SECONDS,
                )

            self._apply_result(record, result)
            logger.info(
                "investigation=%s %s (%s findings)",
                record.id,
                record.status,
                len(record.findings),
            )

        except TimeoutError:
            record.status = "failed"
            record.error_message = (
                f"Investigation exceeded the {GRAPH_WALL_CLOCK_SECONDS}s wall-clock limit."
            )
            self._store.update(record)
            logger.error("investigation=%s timed out", record.id)

        except AppError as exc:
            record.status = "failed"
            record.error_message = exc.message
            self._store.update(record)
            logger.warning("investigation=%s rejected: %s", record.id, exc.code)

        except asyncio.CancelledError:
            record.status = "failed"
            record.error_message = "Investigation was cancelled."
            self._store.update(record)
            raise

        except Exception as exc:
            # Log the type only; the message may contain log content.
            logger.exception("investigation=%s failed: %s", record.id, type(exc).__name__)
            record.status = "failed"
            record.error_message = "The investigation failed unexpectedly."
            self._store.update(record)

        finally:
            # Raw input never outlives the run in memory.
            self._inputs.pop(investigation_id, None)

    def _apply_result(self, record: InvestigationRecord, result: dict[str, Any]) -> None:
        """Copy graph state onto the storable record."""
        summary = result.get("summary")
        if summary is not None:
            record.error_group_count = summary.error_group_count
            record.event_count = summary.total_events
            record.runtime = summary.detected_runtime.value
            record.summary_detail = summary.model_dump(mode="json")

        metadata = result.get("model_metadata") or {}
        record.model_provider = metadata.get("provider")
        record.model_name = metadata.get("model")

        record.warnings = list(result.get("warnings") or [])
        record.processing_steps = list(result.get("processing_steps") or [])
        record.current_step = None
        record.status = result.get("status", "completed")
        record.error_message = result.get("error_message")
        record.completed_at = result.get("completed_at")
        record.duration_seconds = result.get("duration_seconds")
        record.ai_skipped_reason = result.get("ai_skipped_reason")
        record.validation_errors = list(result.get("validation_errors") or [])
        record.model_response = result.get("model_response")
        record.report = result.get("report")
        record.error_groups = list(result.get("groups") or [])
        record.evidence = list(result["evidence"].items) if result.get("evidence") else []

        findings = list(result.get("findings") or [])
        if not findings and record.error_groups:
            # Deterministic fallback so the report is never empty of content.
            from app.graph.nodes import build_fallback_findings

            findings = build_fallback_findings(record.error_groups)
        record.findings = findings

        if summary is not None:
            record.summary = (
                f"{summary.total_events} events from {summary.total_lines} lines; "
                f"{summary.error_group_count} error group(s), "
                f"{summary.warning_group_count} warning group(s)."
            )

        self._store.update(record)


class _NoModel(BaseChatModel):
    """Stand-in used when no provider is configured.

    Every call fails, so the graph takes its deterministic-fallback path and the
    report is explicitly marked partial.
    """

    provider_name = "none"

    def metadata(self):
        """Return metadata naming the absence of a provider."""
        from app.models.base import ModelMetadata

        return ModelMetadata(provider=self.provider_name, model_name="none")

    async def health(self):
        """Report unavailable."""
        from app.models.base import ModelHealth

        return ModelHealth(self.provider_name, "none", False, "No model configured")

    async def complete(self, messages, *, response_schema=None):
        """Always fail: there is no provider to call."""
        from app.models.base import ModelUnavailableError

        raise ModelUnavailableError("No model provider is configured.")


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO timestamp, tolerating None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
