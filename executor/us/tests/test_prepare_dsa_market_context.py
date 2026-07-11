import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from ops.prepare_dsa_market_context import (
    _atomic_write,
    _context_payload,
    _safe_run_id,
)


class PrepareDsaMarketContextTests(unittest.TestCase):
    def test_safe_run_id_is_bounded_and_shell_safe(self) -> None:
        value = _safe_run_id(" us context/2026-07-12 " + "x" * 80)

        self.assertLessEqual(len(value), 32)
        self.assertNotIn("/", value)
        self.assertNotIn(" ", value)

    def test_context_payload_requires_persisted_audit_identifiers(self) -> None:
        context = SimpleNamespace(
            region="us",
            trade_date=date(2026, 7, 10),
            history_id=49,
            query_id="shared-market-us",
            source="analysis_history",
            summary="US market context",
            full_report="Full report",
        )

        payload = _context_payload(context, action="reused")

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["history_id"], 49)
        self.assertEqual(payload["query_id"], "shared-market-us")
        self.assertEqual(payload["trade_date"], "2026-07-10")
        self.assertEqual(len(payload["summary_sha256"]), 64)
        self.assertNotIn("summary", payload)

        context.history_id = None
        with self.assertRaises(RuntimeError):
            _context_payload(context, action="generated")

    def test_atomic_write_replaces_status_with_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "context.json"
            _atomic_write(path, {"status": "ok", "history_id": 7})

            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"history_id": 7, "status": "ok"},
            )
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
