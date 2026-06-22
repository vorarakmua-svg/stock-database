"""Thread-safe rate limiter for API requests."""

import logging
import threading
import time
from functools import wraps
from typing import Callable, Optional, TypeVar

import requests

T = TypeVar('T')

# HTTP status codes worth retrying: rate limiting and transient server errors.
# 404 (not found) and 403 (auth) are deliberately excluded — retrying won't help.
RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})


def is_transient_error(exc: BaseException) -> bool:
    """Return True if an exception represents a transient, retry-worthy failure.

    Network timeouts and connection errors are always transient. HTTP errors are
    transient only for the status codes in :data:`RETRYABLE_STATUS_CODES`.
    """
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        return status in RETRYABLE_STATUS_CODES
    return False


class RateLimiter:
    """
    Thread-safe rate limiter with configurable minimum interval between requests.

    Usage:
        limiter = RateLimiter(min_interval=0.12)  # ~8 requests/second

        # Method 1: Call wait() before each request
        limiter.wait()
        response = requests.get(url)

        # Method 2: Use as decorator
        @limiter.throttle
        def fetch_data(url):
            return requests.get(url)
    """

    def __init__(
        self,
        min_interval: float,
        name: str = "default",
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize the rate limiter.

        Args:
            min_interval: Minimum seconds between requests
            name: Name for logging purposes
            logger: Optional logger instance
        """
        self.min_interval = min_interval
        self.name = name
        self.logger = logger or logging.getLogger(__name__)
        self._last_request: float = 0.0
        self._lock = threading.Lock()
        self._request_count = 0

    def wait(self) -> float:
        """
        Wait if necessary to respect rate limit.

        Returns:
            The number of seconds waited (0 if no wait was needed)
        """
        with self._lock:
            now = time.time()
            elapsed = now - self._last_request
            wait_time = 0.0

            if elapsed < self.min_interval:
                wait_time = self.min_interval - elapsed
                self.logger.debug(
                    f"[{self.name}] Rate limiting: waiting {wait_time:.3f}s"
                )
                time.sleep(wait_time)

            self._last_request = time.time()
            self._request_count += 1

            return wait_time

    def throttle(self, func: Callable[..., T]) -> Callable[..., T]:
        """
        Decorator to throttle function calls.

        Args:
            func: Function to throttle

        Returns:
            Wrapped function with rate limiting
        """
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            self.wait()
            return func(*args, **kwargs)
        return wrapper

    @property
    def request_count(self) -> int:
        """Total number of requests made through this limiter."""
        return self._request_count

    def reset(self) -> None:
        """Reset the rate limiter state."""
        with self._lock:
            self._last_request = 0.0
            self._request_count = 0


class RetryHandler:
    """
    Retry logic with exponential backoff for transient failures.

    Usage:
        retry = RetryHandler(max_retries=3)

        @retry.with_backoff
        def fetch_data():
            return requests.get(url)
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize retry handler.

        Args:
            max_retries: Maximum number of retry attempts
            base_delay: Initial delay between retries in seconds
            max_delay: Maximum delay cap in seconds
            exponential_base: Multiplier for exponential backoff
            logger: Optional logger instance
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.logger = logger or logging.getLogger(__name__)

    def run(
        self,
        func: Callable[..., T],
        *args,
        retryable_exceptions: tuple = (Exception,),
        should_retry: Optional[Callable[[BaseException], bool]] = None,
        **kwargs
    ) -> T:
        """
        Call ``func`` with retries and exponential backoff.

        Args:
            func: The callable to invoke.
            retryable_exceptions: Exception types that may trigger a retry.
            should_retry: Optional predicate; when provided, a caught exception is
                only retried if ``should_retry(exc)`` is True, otherwise it is
                re-raised immediately. Use this to retry transient HTTP errors
                (429/503/...) but fail fast on 404/403.
            *args, **kwargs: Forwarded to ``func``.

        Returns:
            The return value of ``func``.
        """
        last_exception: Optional[BaseException] = None

        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except retryable_exceptions as e:
                if should_retry is not None and not should_retry(e):
                    raise
                last_exception = e

                if attempt == self.max_retries:
                    break

                delay = min(
                    self.base_delay * (self.exponential_base ** attempt),
                    self.max_delay
                )

                self.logger.warning(
                    f"Attempt {attempt + 1}/{self.max_retries + 1} failed: {e}. "
                    f"Retrying in {delay:.1f}s..."
                )

                time.sleep(delay)

        raise last_exception

    def with_backoff(
        self,
        retryable_exceptions: tuple = (Exception,),
        should_retry: Optional[Callable[[BaseException], bool]] = None,
    ) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """
        Decorator form of :meth:`run` for retry logic with exponential backoff.

        Args:
            retryable_exceptions: Tuple of exceptions that trigger retry.
            should_retry: Optional predicate (see :meth:`run`).

        Returns:
            Decorator function
        """
        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            @wraps(func)
            def wrapper(*args, **kwargs) -> T:
                return self.run(
                    func,
                    *args,
                    retryable_exceptions=retryable_exceptions,
                    should_retry=should_retry,
                    **kwargs
                )

            return wrapper
        return decorator
