from dataclasses import dataclass
from pathlib import Path


FILL_MODEL_NEXT_OPEN = "next_open"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = PROJECT_ROOT / "runtime_data"
QUANT_DIR = RUNTIME_DIR / "quant"
ACCEPTANCE_DIR = RUNTIME_DIR / "acceptance"
SECRETS_DIR = RUNTIME_DIR / "secrets"
DSA_DB_PATH = RUNTIME_DIR / "dsa" / "stock_analysis.db"
PAPER_US_DB_PATH = QUANT_DIR / "paper_us.db"
US_MARKET = "us"
US_STOCK_POOL = ("AAPL", "NVDA", "MSFT", "JPM", "SPCX")


@dataclass(frozen=True)
class UsExecutorConfig:
    dsa_db_path: Path = DSA_DB_PATH
    ledger_db_path: Path = PAPER_US_DB_PATH
    disciplined_db_path: Path = PAPER_US_DB_PATH
    use_disciplined_signals: bool = True
    initial_cash: float = 1_000_000.0
    per_signal_cash: float = 100_000.0
    symbol_cap_rate: float = 0.20
    lot_size: int = 1
    fill_model: str = FILL_MODEL_NEXT_OPEN
    slippage_rate: float = 0.001
    open_slippage_multiplier: float = 2.0
    commission_rate: float = 0.0
    min_commission: float = 0.0
    sec_fee_rate: float = 27.80 / 1_000_000
    reduce_exit_rate: float = 0.50
    benchmark_codes: tuple[str, ...] = ("SPY",)
    stock_pool: tuple[str, ...] = US_STOCK_POOL
    market: str = US_MARKET
    t_plus: int = 0
    bar_available_time: str = "16:00"
    bar_available_timezone: str = "America/New_York"
    honor_luld: bool = False
