"""Background job manager for serialized stock-data collection.

Threading model: one ThreadPoolExecutor(max_workers=1) serializes all writes
so only one worker ever holds the SQLite writer at a time. A single
threading.Lock guards both the jobs dict and every mutable field on
JobStatus. Callers always receive snapshots, never live references.
"""
from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..config import AppConfig, StorageConfig
from ..exporters.sqlite_store import SQLiteStore
from ..fetchers.stock_data_fetcher import StockDataFetcher
from ..fetchers.yahoo_handler import YahooHandler

# ---------------------------------------------------------------------------
# Default factories (replaced in tests)
# ---------------------------------------------------------------------------

def default_fetcher_factory(config: AppConfig) -> StockDataFetcher:
    """Create a real StockDataFetcher from the given config."""
    return StockDataFetcher(config)


def default_quote_fetcher_factory(config: AppConfig) -> YahooHandler:
    """Create a real YahooHandler for quote-only refreshes from the given config."""
    return YahooHandler(rate_limit_delay=config.yahoo.rate_limit_delay)


# ---------------------------------------------------------------------------
# Job status
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobStatus:
    """Snapshot of a collection job's current state.

    All fields are plain Python types so ``to_dict()`` can be serialised
    without special encoding. Callers receive copies via ``snapshot()``.
    """

    job_id: str
    tickers: List[str]
    state: str  # "queued" | "running" | "done" | "error"
    submitted_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    total: int = 0
    completed: int = 0
    current_ticker: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "job_id": self.job_id,
            "tickers": list(self.tickers),
            "state": self.state,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total": self.total,
            "completed": self.completed,
            "current_ticker": self.current_ticker,
            "summary": dict(self.summary) if self.summary else None,
            "error": self.error,
        }

    def snapshot(self) -> "JobStatus":
        """Return a shallow copy safe to hand to callers (no live references)."""
        return JobStatus(
            job_id=self.job_id,
            tickers=list(self.tickers),
            state=self.state,
            submitted_at=self.submitted_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            total=self.total,
            completed=self.completed,
            current_ticker=self.current_ticker,
            summary=dict(self.summary) if self.summary else None,
            error=self.error,
        )


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class CollectionJobManager:
    """Serialised background job manager for data collection.

    Guarantees at most one SQLite writer at a time via a
    ``ThreadPoolExecutor(max_workers=1)``. Extra submissions queue until the
    current job finishes.

    Thread safety: every read or write of ``_jobs`` (and of any mutable
    field on ``JobStatus``) happens under ``_lock``. Public methods return
    snapshots, never live ``JobStatus`` objects.
    """

    def __init__(
        self,
        db_path: Path,
        years_back: Optional[int] = None,
        fetcher_factory: Callable[[AppConfig], StockDataFetcher] = default_fetcher_factory,
        quote_fetcher_factory: Callable[[AppConfig], YahooHandler] = default_quote_fetcher_factory,
    ) -> None:
        self.db_path: Path = Path(db_path)
        self.years_back: Optional[int] = years_back
        self.fetcher_factory: Callable[[AppConfig], StockDataFetcher] = fetcher_factory
        self.quote_fetcher_factory: Callable[[AppConfig], YahooHandler] = quote_fetcher_factory
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1)
        self._jobs: Dict[str, JobStatus] = {}
        self._lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(
        self,
        tickers: List[str],
        years_back: Optional[int] = None,
        include_yahoo: bool = True,
        include_sec: bool = True,
        mode: str = "full",
    ) -> str:
        """Enqueue a job and return the ``job_id``.

        ``mode="full"`` (default) runs the existing full collection path
        (``_run``, unchanged). ``mode="quote"`` runs the lightweight
        quote-only refresh path (``_run_quote``) instead — both are submitted
        to the SAME ``ThreadPoolExecutor(max_workers=1)``, so a quote job and
        a full job never run concurrently regardless of submission order.
        """
        job_id = uuid.uuid4().hex
        status = JobStatus(
            job_id=job_id,
            tickers=list(tickers),
            state="queued",
            submitted_at=_now_iso(),
            total=len(tickers),
        )
        with self._lock:
            self._jobs[job_id] = status
        if mode == "quote":
            self._executor.submit(self._run_quote, job_id, list(tickers))
        else:
            effective_years = years_back if years_back is not None else self.years_back
            self._executor.submit(
                self._run, job_id, list(tickers), effective_years, include_yahoo, include_sec
            )
        return job_id

    def get(self, job_id: str) -> Optional[JobStatus]:
        """Return a snapshot of *job_id*, or ``None`` if unknown."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return job.snapshot()

    def list(self) -> List[JobStatus]:
        """Return snapshots of all jobs, newest-submitted first."""
        with self._lock:
            jobs = sorted(
                self._jobs.values(), key=lambda j: j.submitted_at, reverse=True
            )
            return [j.snapshot() for j in jobs]

    def shutdown(self) -> None:
        """Shut down the executor (does not wait for queued work)."""
        self._executor.shutdown(wait=False)

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _run(
        self,
        job_id: str,
        tickers: List[str],
        years_back: Optional[int],
        include_yahoo: bool,
        include_sec: bool,
    ) -> None:
        """Worker function executed by the single-threaded executor.

        Must **never** let an exception propagate out — the executor would
        silently swallow it and the job would stay "running" forever.
        """
        try:
            # Mark running
            with self._lock:
                job = self._jobs[job_id]
                job.state = "running"
                job.started_at = _now_iso()
                job.total = len(tickers)

            config = AppConfig(storage=StorageConfig(db_path=self.db_path))
            fetcher = self.fetcher_factory(config)

            results = []
            with fetcher:
                for ticker in tickers:
                    with self._lock:
                        self._jobs[job_id].current_ticker = ticker
                    result = fetcher.fetch_ticker(
                        ticker,
                        include_yahoo=include_yahoo,
                        include_sec=include_sec,
                        years_back=years_back,
                    )
                    results.append(result)
                    with self._lock:
                        self._jobs[job_id].completed += 1

                # Export once after all tickers fetched
                fetcher.export(results, formats=["sqlite"])

            with self._lock:
                done_job = self._jobs[job_id]
                done_job.state = "done"
                done_job.finished_at = _now_iso()
                done_job.current_ticker = None
                done_job.summary = {
                    "mode": "full",
                    "total": len(results),
                    "successful": sum(1 for r in results if not r.errors),
                    "with_errors": sum(1 for r in results if r.errors),
                }

        except Exception as exc:  # noqa: BLE001 — worker must not propagate
            with self._lock:
                err_job = self._jobs.get(job_id)
                if err_job is not None:
                    err_job.state = "error"
                    err_job.error = str(exc)
                    err_job.finished_at = _now_iso()
                    err_job.current_ticker = None

    def _run_quote(self, job_id: str, tickers: List[str]) -> None:
        """Worker function for ``mode="quote"`` jobs — same executor as ``_run``,
        so it is still serialized with any full-collection job.

        Must **never** let an exception propagate out, for the same reason
        documented on ``_run``. Per-ticker failures are tolerated (counted in
        ``with_errors``) rather than aborting the whole batch, since
        ``YahooHandler.fetch_quote`` reports failures via an ``"error"`` key
        instead of raising.
        """
        try:
            with self._lock:
                job = self._jobs[job_id]
                job.state = "running"
                job.started_at = _now_iso()
                job.total = len(tickers)

            config = AppConfig(storage=StorageConfig(db_path=self.db_path))
            handler = self.quote_fetcher_factory(config)
            store = SQLiteStore(self.db_path)

            successful = 0
            with_errors = 0
            for ticker in tickers:
                with self._lock:
                    self._jobs[job_id].current_ticker = ticker

                quote = handler.fetch_quote(ticker)
                if "error" in quote:
                    with_errors += 1
                else:
                    collected_at = _now_iso()
                    store.upsert_quote(ticker, quote, collected_at)
                    successful += 1

                with self._lock:
                    self._jobs[job_id].completed += 1

            with self._lock:
                done_job = self._jobs[job_id]
                done_job.state = "done"
                done_job.finished_at = _now_iso()
                done_job.current_ticker = None
                done_job.summary = {
                    "mode": "quote",
                    "total": len(tickers),
                    "successful": successful,
                    "with_errors": with_errors,
                }

        except Exception as exc:  # noqa: BLE001 — worker must not propagate
            with self._lock:
                err_job = self._jobs.get(job_id)
                if err_job is not None:
                    err_job.state = "error"
                    err_job.error = str(exc)
                    err_job.finished_at = _now_iso()
                    err_job.current_ticker = None
