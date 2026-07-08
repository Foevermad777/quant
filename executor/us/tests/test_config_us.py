import unittest

from executor.us.config_us import DSA_DB_PATH, PAPER_US_DB_PATH, US_STOCK_POOL, UsExecutorConfig


class UsExecutorConfigTests(unittest.TestCase):
    def test_defaults_are_us_specific_and_isolated(self) -> None:
        config = UsExecutorConfig()

        self.assertEqual(config.dsa_db_path, DSA_DB_PATH)
        self.assertEqual(config.ledger_db_path, PAPER_US_DB_PATH)
        self.assertEqual(config.disciplined_db_path, PAPER_US_DB_PATH)
        self.assertEqual(config.ledger_db_path.name, "paper_us.db")
        self.assertEqual(config.market, "us")
        self.assertEqual(config.stock_pool, US_STOCK_POOL)
        self.assertEqual(config.benchmark_codes, ("SPY",))

    def test_trading_rules_are_us_defaults(self) -> None:
        config = UsExecutorConfig()

        self.assertEqual(config.t_plus, 0)
        self.assertEqual(config.lot_size, 1)
        self.assertEqual(config.initial_cash, 1_000_000.0)
        self.assertEqual(config.per_signal_cash, 100_000.0)
        self.assertEqual(config.symbol_cap_rate, 0.20)
        self.assertFalse(config.honor_luld)
        self.assertEqual(config.bar_available_time, "16:00")
        self.assertEqual(config.bar_available_timezone, "America/New_York")

    def test_fee_config_has_sec_fee_but_no_stamp_tax_concept(self) -> None:
        config = UsExecutorConfig()

        self.assertEqual(config.commission_per_share, 0.005)
        self.assertEqual(config.min_commission, 1.0)
        self.assertEqual(config.commission_rate, 0.0)
        self.assertGreater(config.sec_fee_rate, 0)
        self.assertFalse(hasattr(config, "stamp_tax_rate"))


if __name__ == "__main__":
    unittest.main()
