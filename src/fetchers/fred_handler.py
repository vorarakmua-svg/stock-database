"""FRED (Federal Reserve Economic Data) handler for economic indicators.

Provides risk-free rate (Treasury yields) and other economic data for financial modeling.
FRED API is free but requires registration at https://fred.stlouisfed.org/docs/api/api_key.html
"""

import logging
import os
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

import requests

from .rate_limiter import RateLimiter, RetryHandler, is_transient_error


class FREDHandler:
    """
    Handler for FRED (Federal Reserve Economic Data) API.

    Fetches Treasury yields for risk-free rate calculations in DCF models.

    Key Series:
    - DGS10: 10-Year Treasury Constant Maturity Rate
    - DGS5: 5-Year Treasury Constant Maturity Rate
    - DGS2: 2-Year Treasury Constant Maturity Rate
    - DGS30: 30-Year Treasury Constant Maturity Rate
    - FEDFUNDS: Federal Funds Effective Rate
    """

    FRED_BASE_URL = "https://api.stlouisfed.org/fred"

    # Common Treasury series IDs
    TREASURY_SERIES = {
        "1m": "DGS1MO",
        "3m": "DGS3MO",
        "6m": "DGS6MO",
        "1y": "DGS1",
        "2y": "DGS2",
        "5y": "DGS5",
        "10y": "DGS10",
        "20y": "DGS20",
        "30y": "DGS30",
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
        rate_limit_delay: float = 0.5,
        logger: Optional[logging.Logger] = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
    ):
        """
        Initialize FRED handler.

        Args:
            api_key: FRED API key (get free at https://fred.stlouisfed.org/docs/api/api_key.html)
                    Can also be set via FRED_API_KEY environment variable
            rate_limit_delay: Minimum seconds between requests
            logger: Optional logger instance
            max_retries: Max retry attempts for transient network failures
            base_delay: Initial backoff delay in seconds
            max_delay: Maximum backoff delay cap in seconds
        """
        self.api_key = api_key or os.getenv("FRED_API_KEY")
        self.logger = logger or logging.getLogger(__name__)
        self.rate_limiter = RateLimiter(
            min_interval=rate_limit_delay,
            name="fred"
        )
        self.retry_handler = RetryHandler(
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay,
            logger=self.logger
        )

        self.session = requests.Session()

        if not self.api_key:
            self.logger.warning(
                "No FRED API key provided. Set FRED_API_KEY environment variable "
                "or pass api_key parameter. Get free key at: "
                "https://fred.stlouisfed.org/docs/api/api_key.html"
            )

    def _request(self, url: str, params: Optional[Dict[str, Any]] = None,
                 timeout: int = 30) -> requests.Response:
        """Perform a rate-limited GET with retries on transient failures.

        Retries timeouts, connection errors, and retryable HTTP statuses; other
        responses are returned as-is. Raises ``RequestException`` only when
        transient retries are exhausted.
        """
        def do_get() -> requests.Response:
            self.rate_limiter.wait()
            response = self.session.get(url, params=params, timeout=timeout)
            if response.status_code in (429, 502, 503, 504):
                response.raise_for_status()
            return response

        return self.retry_handler.run(
            do_get,
            retryable_exceptions=(requests.exceptions.RequestException,),
            should_retry=is_transient_error,
        )

    def get_treasury_yields(self) -> Dict[str, Any]:
        """
        Get current Treasury yields for all maturities.

        Returns:
            Dictionary with Treasury yields by maturity
        """
        if not self.api_key:
            return self._get_fallback_yields()

        yields = {
            "fetched_at": datetime.now().isoformat(),
            "source": "fred",
        }

        for maturity, series_id in self.TREASURY_SERIES.items():
            try:
                data = self.get_series_latest(series_id)
                if data:
                    yields[f"treasury_{maturity}"] = data.get("value")
                    yields[f"treasury_{maturity}_date"] = data.get("date")
            except Exception as e:
                self.logger.warning(f"Error fetching {series_id}: {e}")

        return yields

    def get_risk_free_rate(self, maturity: str = "10y") -> Optional[float]:
        """
        Get risk-free rate (Treasury yield) for specified maturity.

        Args:
            maturity: Treasury maturity (1m, 3m, 6m, 1y, 2y, 5y, 10y, 20y, 30y)

        Returns:
            Risk-free rate as decimal (e.g., 0.045 for 4.5%)
        """
        series_id = self.TREASURY_SERIES.get(maturity)
        if not series_id:
            self.logger.error(f"Invalid maturity: {maturity}")
            return None

        if not self.api_key:
            fallback = self._get_fallback_yields()
            rate = fallback.get(f"treasury_{maturity}")
            if rate:
                return rate / 100  # Convert percentage to decimal
            return None

        data = self.get_series_latest(series_id)
        if data and data.get("value"):
            return data["value"] / 100  # Convert percentage to decimal
        return None

    def get_series_latest(self, series_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the latest value for a FRED series.

        Args:
            series_id: FRED series ID (e.g., 'DGS10')

        Returns:
            Dictionary with date and value, or None on error
        """
        if not self.api_key:
            return None

        url = f"{self.FRED_BASE_URL}/series/observations"
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 10,  # Get last 10 to find most recent non-null value
        }

        try:
            response = self._request(url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            observations = data.get("observations", [])

            # Find most recent non-null value
            for obs in observations:
                value = obs.get("value")
                if value and value != ".":
                    return {
                        "date": obs.get("date"),
                        "value": float(value),
                        "series_id": series_id,
                    }

            return None

        except Exception as e:
            self.logger.error(f"Error fetching FRED series {series_id}: {e}")
            return None

    def get_series_history(
        self,
        series_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get historical data for a FRED series.

        Args:
            series_id: FRED series ID
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            List of observations with date and value
        """
        if not self.api_key:
            return []

        url = f"{self.FRED_BASE_URL}/series/observations"
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
        }

        if start_date:
            params["observation_start"] = start_date
        if end_date:
            params["observation_end"] = end_date

        try:
            response = self._request(url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            observations = data.get("observations", [])

            return [
                {
                    "date": obs.get("date"),
                    "value": float(obs.get("value")) if obs.get("value") and obs.get("value") != "." else None,
                }
                for obs in observations
            ]

        except Exception as e:
            self.logger.error(f"Error fetching FRED series history: {e}")
            return []

    def _get_fallback_yields(self) -> Dict[str, Any]:
        """
        Get fallback Treasury yields when no API key is available.
        Uses Treasury.gov data or returns typical estimates.

        Returns:
            Dictionary with estimated Treasury yields
        """
        self.logger.info("Using fallback Treasury yields (no FRED API key)")

        # Try to fetch from Treasury.gov CSV feed (no API key required). The feed is
        # organized by calendar year; try the current year, then fall back to the
        # prior year (the current-year feed is empty in early January).
        for year in (datetime.now().year, datetime.now().year - 1):
            yields = self._fetch_treasury_gov_year(year)
            if yields:
                return yields

        # Return typical market estimates as last resort. These are static and
        # quickly go stale, so warn loudly — downstream WACC/DCF must know the
        # risk-free rate is not live.
        self.logger.warning(
            "Falling back to static Treasury yield estimates; these are not "
            "current. Set FRED_API_KEY for live data."
        )
        return {
            "fetched_at": datetime.now().isoformat(),
            "source": "estimate",
            "note": "Estimated values - get FRED API key for real-time data",
            "treasury_3m": 5.25,
            "treasury_6m": 5.15,
            "treasury_1y": 4.85,
            "treasury_2y": 4.45,
            "treasury_5y": 4.25,
            "treasury_10y": 4.35,
            "treasury_20y": 4.65,
            "treasury_30y": 4.55,
        }

    def _fetch_treasury_gov_year(self, year: int) -> Optional[Dict[str, Any]]:
        """Fetch the daily Treasury yield curve CSV for a calendar ``year``.

        Returns a yields dict (latest row) or None if unavailable/empty.
        """
        url = (
            "https://home.treasury.gov/resource-center/data-chart-center/"
            f"interest-rates/daily-treasury-rates.csv/{year}/all"
            f"?type=daily_treasury_yield_curve&field_tdr_date_value={year}"
            "&page&_format=csv"
        )

        try:
            response = self._request(url, timeout=30)

            if response.status_code == 200:
                lines = response.text.strip().split('\n')
                if len(lines) > 1:
                    headers = lines[0].split(',')
                    latest = lines[-1].split(',')

                    yields = {
                        "fetched_at": datetime.now().isoformat(),
                        "source": "treasury_gov",
                    }

                    col_map = {
                        "1 Mo": "1m", "2 Mo": "2m", "3 Mo": "3m",
                        "6 Mo": "6m", "1 Yr": "1y", "2 Yr": "2y",
                        "3 Yr": "3y", "5 Yr": "5y", "7 Yr": "7y",
                        "10 Yr": "10y", "20 Yr": "20y", "30 Yr": "30y",
                    }

                    for i, header in enumerate(headers):
                        header = header.strip().strip('"')
                        if header in col_map and i < len(latest):
                            try:
                                value = float(latest[i].strip().strip('"'))
                                yields[f"treasury_{col_map[header]}"] = value
                            except (ValueError, IndexError):
                                pass

                    if len(yields) > 2:  # More than just fetched_at and source
                        return yields

        except Exception as e:
            self.logger.debug(f"Treasury.gov fallback failed for {year}: {e}")

        return None

    def get_market_risk_premium(self) -> Dict[str, Any]:
        """
        Get market risk premium estimates.

        Note: This is typically an assumption (5-7% historical average).
        Returns commonly used values for DCF modeling.

        Returns:
            Dictionary with market risk premium estimates
        """
        return {
            "historical_average": 0.055,  # 5.5% - long-term historical
            "current_estimate": 0.05,      # 5.0% - current market estimate
            "conservative": 0.06,          # 6.0% - conservative assumption
            "aggressive": 0.045,           # 4.5% - aggressive assumption
            "source": "historical_estimate",
            "note": "Market risk premium is typically assumed at 5-6% based on historical equity returns minus risk-free rate",
        }

    def close(self) -> None:
        """Close the session."""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
