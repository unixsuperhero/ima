import unittest

from now.engine import BREAK_S, IDLE_S, OVERRUN_S, Session, tick


class EngineTest(unittest.TestCase):
    def test_idle_after_three_minutes(self):
        s = Session(started_at=0, last_activity=0)
        self.assertIsNone(tick(s, IDLE_S - 1, 0))
        self.assertEqual(tick(s, IDLE_S, 0), "idle")

    def test_overrun_with_no_breaks(self):
        s = Session(started_at=0, last_activity=0)
        self.assertIsNone(tick(s, OVERRUN_S - 1, OVERRUN_S - 1))
        self.assertEqual(tick(s, OVERRUN_S, OVERRUN_S), "overrun")

    def test_break_prevents_overrun(self):
        s = Session(started_at=0, last_activity=0)
        tick(s, 30, 30)
        tick(s, 30 + BREAK_S, 30 + BREAK_S)  # a one-minute gap
        self.assertEqual(s.breaks, 1)
        self.assertIsNone(tick(s, OVERRUN_S + 10, OVERRUN_S + 10))

    def test_short_gap_is_not_a_break(self):
        s = Session(started_at=0, last_activity=0)
        tick(s, 30, 30)
        tick(s, 30 + BREAK_S - 1, 30 + BREAK_S - 1)
        self.assertEqual(s.breaks, 0)


if __name__ == "__main__":
    unittest.main()
