from dataclasses import dataclass
from pathlib import Path
from typing import Optional


FILL_MODEL_NEXT_OPEN = "next_open"
FILL_MODEL_LIMIT_ENTRY_HIGH = "limit_entry_high"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = PROJECT_ROOT / "runtime_data"
QUANT_DIR = RUNTIME_DIR / "quant"
ACCEPTANCE_DIR = RUNTIME_DIR / "acceptance"
SECRETS_DIR = RUNTIME_DIR / "secrets"
DSA_DB_PATH = RUNTIME_DIR / "dsa" / "stock_analysis.db"
PAPER_DB_PATH = QUANT_DIR / "paper.db"
GEMINI_API_KEY_PATH = SECRETS_DIR / "gemini_api_key.txt"
DEEPSEEK_API_KEY_PATH = SECRETS_DIR / "deepseek_api_key.txt"
DISCIPLINE_SKILL_PATH = PROJECT_ROOT / "dsa_skills" / "discipline.yaml"
G5_DEFAULT_MODEL = "gemini-3.5-flash"
G5_DEFAULT_FALLBACK_MODEL = "deepseek-chat"
G5_COMPLETION_VERSION = "g5-minimal-v0.1"
G5_SCHEMA_VERSION = "g5-discipline-v0.1"


@dataclass(frozen=True)
class ExecutorConfig:
    dsa_db_path: Path = DSA_DB_PATH
    ledger_db_path: Path = PAPER_DB_PATH
    disciplined_db_path: Optional[Path] = None
    use_disciplined_signals: bool = True
    initial_cash: float = 1_000_000.0
    per_signal_cash: float = 100_000.0
    symbol_cap_rate: float = 0.20
    lot_size: int = 100
    fill_model: str = FILL_MODEL_NEXT_OPEN
    slippage_rate: float = 0.001
    open_slippage_multiplier: float = 2.0
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    reduce_exit_rate: float = 0.50
    block_limit_up_open: bool = True
    block_limit_down_open: bool = True
    benchmark_codes: tuple = ("000300", "399300", "SH000300")
    stock_pool: tuple = ("600519", "300750", "601318", "600036", "600900")
