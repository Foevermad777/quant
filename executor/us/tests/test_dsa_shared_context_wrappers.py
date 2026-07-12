import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[3]


class DsaSharedContextWrapperTests(unittest.TestCase):
    def _run_wrapper(
        self,
        wrapper_name: str,
        *,
        market: str,
        business_success: bool,
    ) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]], list[str], dict[str, object]]:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            dsa_dir = temp_root / "dsa"
            dsa_dir.mkdir()
            calls_path = temp_root / "stock_calls.jsonl"
            context_calls_path = temp_root / "context_calls.txt"
            context_script = temp_root / "fake_context.py"
            main_script = temp_root / "fake_main.py"

            context_script.write_text(
                """import argparse
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--region', required=True)
parser.add_argument('--output', required=True)
args, _ = parser.parse_known_args()
with Path(os.environ['CONTEXT_CALLS']).open('a', encoding='utf-8') as handle:
    handle.write(args.region + '\\n')
Path(args.output).write_text(json.dumps({
    'status': 'ok',
    'action': 'generated',
    'region': args.region,
    'trade_date': '2026-07-10',
    'history_id': 901,
    'query_id': f'shared-test-{args.region}',
}), encoding='utf-8')
""",
                encoding="utf-8",
            )
            main_script.write_text(
                """import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
stock = args[args.index('--stocks') + 1]
query_id = args[args.index('--market-context-query-id') + 1]
with Path(os.environ['STOCK_CALLS']).open('a', encoding='utf-8') as handle:
    handle.write(json.dumps({'stock': stock, 'query_id': query_id, 'args': args}) + '\\n')
if os.environ['FAKE_BUSINESS_SUCCESS'] == '1':
    print('成功: 1, 失败: 0')
else:
    print('成功: 0, 失败: 1')
""",
                encoding="utf-8",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PROJECT_DIR": str(temp_root),
                    "DSA_DIR": str(dsa_dir),
                    "PYTHON_BIN": sys.executable,
                    "DSA_MAIN": str(main_script),
                    "CAFFEINATE_BIN": "",
                    "CONTEXT_CALLS": str(context_calls_path),
                    "STOCK_CALLS": str(calls_path),
                    "FAKE_BUSINESS_SUCCESS": "1" if business_success else "0",
                }
            )
            if market == "cn":
                env.update(
                    {
                        "DSA_STOCKS": "600519,300750,601318,600036,600900",
                        "DSA_SKIP_PROXY_CHECK": "1",
                        "DSA_FORCE_RUN": "1",
                        "DSA_PREFLIGHT_ENABLED": "0",
                        "DSA_DB_VERIFY_ENABLED": "0",
                        "DSA_ALERT_NOTIFY": "0",
                        "DSA_MARKET_CONTEXT_NOTIFY": "0",
                        "DSA_MARKET_CONTEXT_SCRIPT": str(context_script),
                    }
                )
                status_pattern = "dsa_daily_status_*.json"
            else:
                env.update(
                    {
                        "US_STOCKS": "AAPL,NVDA,MSFT,JPM,SPCX",
                        "US_DSA_SKIP_PROXY_CHECK": "1",
                        "US_DSA_FORCE_RUN": "1",
                        "US_DSA_PREFLIGHT_ENABLED": "0",
                        "US_DSA_DB_VERIFY_ENABLED": "0",
                        "US_DSA_ALERT_NOTIFY": "0",
                        "US_DSA_MARKET_CONTEXT_NOTIFY": "0",
                        "US_DSA_MARKET_CONTEXT_SCRIPT": str(context_script),
                    }
                )
                status_pattern = "us_dsa_daily_status_*.json"

            completed = subprocess.run(
                ["/bin/bash", str(PROJECT_DIR / "ops" / wrapper_name)],
                cwd=PROJECT_DIR,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            calls = [
                json.loads(line)
                for line in calls_path.read_text(encoding="utf-8").splitlines()
            ]
            context_calls = context_calls_path.read_text(encoding="utf-8").splitlines()
            status_paths = list(
                (temp_root / "runtime_data" / "logs").glob(status_pattern)
            )
            self.assertEqual(len(status_paths), 1)
            status = json.loads(status_paths[0].read_text(encoding="utf-8"))
            return completed, calls, context_calls, status

    def test_both_markets_prepare_once_and_share_context_across_five_stocks(self) -> None:
        cases = (
            ("run_dsa_daily.sh", "cn", "shared-test-cn"),
            ("run_us_dsa_daily.sh", "us", "shared-test-us"),
        )

        for wrapper_name, market, expected_query_id in cases:
            with self.subTest(market=market):
                completed, calls, context_calls, status = self._run_wrapper(
                    wrapper_name,
                    market=market,
                    business_success=True,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(context_calls, [market])
                self.assertEqual(len(calls), 5)
                self.assertTrue(all(call["query_id"] == expected_query_id for call in calls))
                self.assertTrue(all("--no-market-review" in call["args"] for call in calls))
                self.assertTrue(all("--reuse-market-context" in call["args"] for call in calls))
                self.assertEqual(status["status"], "ok")
                self.assertEqual(status["success"], 5)
                self.assertEqual(status["market_context_query_id"], expected_query_id)
                self.assertEqual(status["market_context_history_id"], "901")

    def test_both_markets_alert_when_all_business_calls_fail_with_exit_zero(self) -> None:
        for wrapper_name, market in (
            ("run_dsa_daily.sh", "cn"),
            ("run_us_dsa_daily.sh", "us"),
        ):
            with self.subTest(market=market):
                completed, calls, context_calls, status = self._run_wrapper(
                    wrapper_name,
                    market=market,
                    business_success=False,
                )

                self.assertEqual(completed.returncode, 70, completed.stderr)
                self.assertEqual(context_calls, [market])
                self.assertEqual(len(calls), 5)
                self.assertEqual(status["status"], "alert")
                self.assertEqual(status["success"], 0)
                self.assertEqual(status["failed"], 5)
                self.assertEqual(status["exit_code"], 70)


if __name__ == "__main__":
    unittest.main()
