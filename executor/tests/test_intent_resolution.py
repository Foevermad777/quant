import unittest

from executor.intent_resolution import (
    classify_conflict_status,
    corrected_conflict_status,
    resolve_intent,
)


class ClassifyConflictStatusTests(unittest.TestCase):
    def test_split_text_with_conditional_plan_is_conditional_entry(self) -> None:
        # 2026-07-21 collision fix: this exact shape ("holders hold, flat
        # accounts buy the pullback") used to land in position_context_split
        # because the split branch was evaluated first and its predicate is
        # always true for the constant (watch, hold) pair G5 emits.
        status = classify_conflict_status(
            signal_action="buy",
            operation_advice="持仓者继续持有；空仓者等待回踩支撑位后分批买入。",
            flat_account_action="watch",
            holding_action="hold",
            resolved_action="watch",
        )
        self.assertEqual(status, "conditional_entry")

    def test_split_text_without_entry_condition_stays_split(self) -> None:
        status = classify_conflict_status(
            signal_action="buy",
            operation_advice="持仓者继续持有；空仓者观望，不参与。",
            flat_account_action="watch",
            holding_action="hold",
            resolved_action="watch",
        )
        self.assertEqual(status, "position_context_split")

    def test_flat_exit_never_becomes_conditional_entry(self) -> None:
        status = classify_conflict_status(
            signal_action="buy",
            operation_advice="持仓者持有；空仓者回避，若回踩也不建议参与。",
            flat_account_action="avoid",
            holding_action="hold",
            resolved_action="watch",
        )
        self.assertEqual(status, "position_context_split")

    def test_plain_watch_text_still_hard_conflicts(self) -> None:
        status = classify_conflict_status(
            signal_action="buy",
            operation_advice="观望。",
            flat_account_action="watch",
            holding_action="watch",
            resolved_action="watch",
        )
        self.assertEqual(status, "hard_conflict")

    def test_legacy_path_promotes_conditional_split_text(self) -> None:
        # Documented delta of the fix: on the no-G5 legacy path this shape used
        # to collapse into hard_conflict via the split branch; it now carries
        # the executable plan forward as a conditional entry.
        resolution = resolve_intent(
            signal_action="buy",
            operation_advice="持仓者继续持有；空仓者等待回踩支撑位后分批买入。",
            metadata=None,
        )
        self.assertEqual(resolution.conflict_status, "conditional_entry")
        self.assertEqual(resolution.source, "legacy_operation_advice")


class CorrectedConflictStatusTests(unittest.TestCase):
    def _corrected(self, **overrides):
        kwargs = {
            "conflict_status": "position_context_split",
            "conflict_reason": "空仓者等待企稳后在335-338美元支撑区间逢低分批吸纳。",
            "signal_action": "buy",
            "flat_account_action": "watch",
            "has_executable_entry_plan": True,
        }
        kwargs.update(overrides)
        return corrected_conflict_status(**kwargs)

    def test_pcs_with_conditional_reason_and_plan_reclassifies(self) -> None:
        self.assertEqual(self._corrected(), "conditional_entry")

    def test_english_zone_phrasing_reclassifies(self) -> None:
        self.assertEqual(
            self._corrected(conflict_reason="Flat accounts should wait for a pullback into the 335-338 support range."),
            "conditional_entry",
        )

    def test_reason_without_conditional_evidence_stays_split(self) -> None:
        self.assertEqual(
            self._corrected(conflict_reason="空仓者不宜参与，风险过高。"),
            "position_context_split",
        )

    def test_missing_entry_plan_stays_split(self) -> None:
        self.assertEqual(self._corrected(has_executable_entry_plan=False), "position_context_split")

    def test_flat_exit_action_stays_split(self) -> None:
        self.assertEqual(self._corrected(flat_account_action="avoid"), "position_context_split")

    def test_non_entry_signal_stays_split(self) -> None:
        self.assertEqual(self._corrected(signal_action="hold"), "position_context_split")

    def test_non_split_statuses_pass_through(self) -> None:
        for status in ("consistent", "hard_conflict", "conditional_entry"):
            self.assertEqual(self._corrected(conflict_status=status), status)


if __name__ == "__main__":
    unittest.main()
