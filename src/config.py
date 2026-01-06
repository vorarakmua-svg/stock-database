"""Configuration settings for the Stock Data Collection System."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class SECConfig:
    """SEC EDGAR API configuration."""
    base_url: str = "https://data.sec.gov"
    # IMPORTANT: Update this with your actual company name and email
    # SEC requires this format for all requests
    user_agent: str = field(
        default_factory=lambda: os.getenv(
            "SEC_USER_AGENT",
            "StockDataCollector vorarak.mua@gmail.com"
        )
    )
    # SEC allows max 10 requests/second
    rate_limit_delay: float = 0.12  # ~8 requests/second to be safe

    @property
    def company_facts_url(self) -> str:
        """URL template for company facts (XBRL data)."""
        return f"{self.base_url}/api/xbrl/companyfacts/CIK{{cik}}.json"

    @property
    def submissions_url(self) -> str:
        """URL template for company submissions."""
        return f"{self.base_url}/submissions/CIK{{cik}}.json"

    @property
    def tickers_url(self) -> str:
        """URL for ticker to CIK mapping."""
        return "https://www.sec.gov/files/company_tickers.json"


@dataclass
class YahooConfig:
    """Yahoo Finance configuration."""
    # Conservative delay to avoid rate limiting (unofficial API)
    rate_limit_delay: float = 2.5
    # Default historical data period
    default_period: str = "5y"
    default_interval: str = "1d"


@dataclass
class StorageConfig:
    """Storage and output configuration."""
    base_dir: Path = field(default_factory=lambda: Path("data"))

    @property
    def output_dir(self) -> Path:
        return self.base_dir / "output"

    @property
    def json_dir(self) -> Path:
        return self.output_dir / "json"

    @property
    def csv_dir(self) -> Path:
        return self.output_dir / "csv"

    @property
    def cache_dir(self) -> Path:
        return self.base_dir / "cache"

    def ensure_directories(self) -> None:
        """Create all required directories."""
        self.json_dir.mkdir(parents=True, exist_ok=True)
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


@dataclass
class RetryConfig:
    """Retry and error handling configuration."""
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0


@dataclass
class AppConfig:
    """Main application configuration."""
    sec: SECConfig = field(default_factory=SECConfig)
    yahoo: YahooConfig = field(default_factory=YahooConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)

    # Logging
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )

    # Output formats
    output_formats: List[str] = field(
        default_factory=lambda: ["json", "csv"]
    )

    def __post_init__(self):
        """Ensure directories exist after initialization."""
        self.storage.ensure_directories()


# Default configuration instance
default_config = AppConfig()
