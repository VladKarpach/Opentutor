"""Tests for the plan diff and its reasons — TZ FR-10.4.

The whole module is pure arithmetic over two snapshots plus string formatting, so
it is tested without a database. The parts that need one (snapshot, record,
resolve) are covered by the live check instead.
"""

from datetime import date, timedelta

import pytest

from services.adaptive_learning.plan_diff import (
    KIND_ADDED,
    KIND_MINUTES,
    KIND_MOVED,
    KIND_REMOVED,
    MIN_DAYS_SHIFT,
    MIN_MINUTES_DELTA,
    SnapshotItem,
    describe,
    diff,
    reason_for,
    summary_line,
)

TODAY = date(2026, 3, 10)


def item(topic="Тема A", item_type="study", day_offset=0, minutes=30, tid="t1"):
    return SnapshotItem(
        key=(tid, item_type),
        topic_id=tid,
        topic_title=topic,
        item_type=item_type,
        day=TODAY + timedelta(days=day_offset),
        planned_minutes=minutes,
    )


class TestNoChange:
    """An identical replan must not produce a revision."""

    def test_identical_snapshots_are_empty(self):
        before = [item(), item(tid="t2")]
        after = [item(), item(tid="t2")]
        assert diff(before, after).is_empty

    def test_empty_to_empty_is_empty(self):
        assert diff([], []).is_empty

    def test_tiny_minutes_shift_is_noise(self):
        """A 2-minute wobble is not worth interrupting the learner for."""
        before = [item(minutes=30)]
        after = [item(minutes=30 + MIN_MINUTES_DELTA - 1)]
        assert diff(before, after).is_empty

    def test_same_day_shift_is_noise(self):
        before = [item(day_offset=0)]
        after = [item(day_offset=MIN_DAYS_SHIFT - 1)]
        assert diff(before, after).is_empty


class TestAddedAndRemoved:
    def test_new_key_is_added(self):
        result = diff([], [item(minutes=40)])
        assert len(result.changes) == 1
        assert result.changes[0]["kind"] == KIND_ADDED
        assert result.total_minutes_added == 40

    def test_vanished_key_is_removed(self):
        result = diff([item(minutes=40)], [])
        assert result.changes[0]["kind"] == KIND_REMOVED
        assert result.total_minutes_removed == 40

    def test_added_and_removed_are_separate(self):
        result = diff([item(tid="gone")], [item(tid="new")])
        kinds = {c["kind"] for c in result.changes}
        assert kinds == {KIND_ADDED, KIND_REMOVED}


class TestMoved:
    def test_date_shift_is_a_move(self):
        result = diff([item(day_offset=0)], [item(day_offset=3)])
        assert result.changes[0]["kind"] == KIND_MOVED
        assert result.changes[0]["before"] == TODAY.isoformat()

    def test_move_wins_over_a_minutes_change(self):
        """One row that both moved and shrank is one change, not two."""
        before = [item(day_offset=0, minutes=60)]
        after = [item(day_offset=2, minutes=25)]
        result = diff(before, after)
        assert len(result.changes) == 1
        assert result.changes[0]["kind"] == KIND_MOVED

    def test_move_still_accounts_for_the_minutes(self):
        before = [item(day_offset=0, minutes=60)]
        after = [item(day_offset=2, minutes=25)]
        result = diff(before, after)
        assert result.total_minutes_removed == 35


class TestMinutes:
    def test_growth_is_reported(self):
        result = diff([item(minutes=30)], [item(minutes=60)])
        assert result.changes[0]["kind"] == KIND_MINUTES
        assert result.total_minutes_added == 30

    def test_shrink_is_reported_as_negative_delta(self):
        result = diff([item(minutes=60)], [item(minutes=25)])
        assert result.changes[0]["delta_minutes"] == -35
        assert result.total_minutes_removed == 35


class TestOrdering:
    """The biggest change should be listed first."""

    def test_largest_delta_comes_first(self):
        before = [item(tid="a", minutes=60), item(tid="b", minutes=60), item(tid="c", minutes=60)]
        after = [item(tid="a", minutes=25), item(tid="b", minutes=30), item(tid="c", minutes=60)]
        result = diff(before, after)
        deltas = [abs(c["delta_minutes"]) for c in result.changes]
        assert deltas == sorted(deltas, reverse=True)


class TestDescribe:
    """The wording the TZ gives as an example."""

    def test_added_reads_like_the_spec(self):
        change = diff([], [item(topic="Метод Гаусса", minutes=30)]).changes[0]
        text = describe(change)
        assert "добавлено 30 мин" in text
        assert "Метод Гаусса" in text

    def test_review_item_is_named(self):
        change = diff([], [item(item_type="review", minutes=30)]).changes[0]
        assert "повторение" in describe(change)

    def test_removed_states_the_absolute_amount(self):
        change = diff([item(minutes=45)], []).changes[0]
        text = describe(change)
        assert "убрано 45 мин" in text
        assert "-" not in text

    def test_moved_states_the_new_date(self):
        change = diff([item(day_offset=0)], [item(day_offset=2)]).changes[0]
        text = describe(change)
        assert "перенесено" in text
        assert (TODAY + timedelta(days=2)).isoformat() in text

    def test_shrunk_item_reads_as_removed_time(self):
        change = diff([item(minutes=60)], [item(minutes=25)]).changes[0]
        text = describe(change)
        assert "убрано 35 мин" in text


class TestSummary:
    def test_empty_change_says_nothing_changed(self):
        assert summary_line([], trigger="answer") == "План не изменился"

    def test_summary_names_the_trigger(self):
        line = summary_line(
            diff([], [item(minutes=30)]).changes, trigger="answer"
        )
        assert "после проверки знаний" in line

    def test_settings_trigger_is_named(self):
        line = summary_line(
            diff([], [item(minutes=30)]).changes, trigger="settings"
        )
        assert "после изменения настроек" in line

    def test_context_is_appended(self):
        line = summary_line(
            diff([], [item(minutes=30)]).changes,
            trigger="settings",
            context="бюджет 60 → 25 мин/день",
        )
        assert "60 → 25" in line

    def test_total_delta_is_reported(self):
        line = summary_line(
            diff([item(minutes=60)], [item(minutes=25)]).changes, trigger="replan"
        )
        assert "35 мин" in line


class TestReasons:
    """FR-10.2 triggers must produce a human reason, never an empty string."""

    @pytest.mark.parametrize(
        "trigger",
        ["answer", "settings", "goal_change", "catch_up", "manual", "replan", "unknown"],
    )
    def test_every_trigger_has_a_reason(self, trigger):
        assert reason_for(trigger).strip()

    def test_answer_trigger_mentions_intervals(self):
        assert "интервал" in reason_for("answer")

    def test_reasons_are_russian(self):
        # A guard against slipping back to an English placeholder.
        for trigger in ("answer", "settings", "goal_change", "catch_up", "manual"):
            text = reason_for(trigger)
            assert any("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in text), trigger


class TestKeying:
    def test_same_topic_different_type_are_different_rows(self):
        before = [item(item_type="study", tid="t1")]
        after = [item(item_type="review", tid="t1")]
        result = diff(before, after)
        assert {c["kind"] for c in result.changes} == {KIND_ADDED, KIND_REMOVED}

    def test_different_topics_are_compared_independently(self):
        before = [item(tid="a", minutes=30)]
        after = [item(tid="a", minutes=30), item(tid="b", minutes=30)]
        result = diff(before, after)
        assert len(result.changes) == 1
        assert result.changes[0]["topic_id"] == "b"