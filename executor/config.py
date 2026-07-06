from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = PROJECT_ROOT / "runtime_data"
QUANT_DIR = RUNTIME_DIR / "quant"
ACCEPTANCE_DIR = RUNTIME_DIR / "acceptance"
DSA_DB_PATH = RUNTIME_DIR / "dsa" / "stock_analysis.db"
PAPER_DB_PATH = QUANT_DIR / "paper.db"


@dataclass(frozen=True)
class ExecutorConfig:
    dsa_db_path: Path = DSA_DB_PATH
    ledger_db_path: Path = PAPER_DB_PATH
    initial_cash: float = 1_000_000.0
    per_signal_cash: float = 100_000.0
    symbol_cap_rate: float = 0.20
    lot_size: int = 100
    slippage_rate: float = 0.001
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    reduce_exit_rate: float = 0.50
    block_limit_up_open: bool = True
    block_limit_down_open: bool = True
    benchmark_codes: tuple = ("000300", "399300", "SH000300")
    stock_pool: tuple = ("600519", "300750", "601318", "600036", "600900")
