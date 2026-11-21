import unittest

from router.ranking import ProbeResult, rank_candidates


class RankingTests(unittest.TestCase):
    def test_target_priority_precedes_latency_and_excludes_failures(self):
        results = [
            ProbeResult("fast", "fast", frozenset({"youtube"}), 10),
            ProbeResult("slow", "slow", frozenset({"google"}), 500),
            ProbeResult("fail", "fail", frozenset(), 1),
        ]
        self.assertEqual(
            [result.node_id for result in rank_candidates(results, ["google", "youtube"])],
            ["slow", "fast"],
        )

    def test_missing_latency_and_empty_target_selection(self):
        results = [
            ProbeResult("unknown", "A", frozenset({"google"}), None),
            ProbeResult("known", "B", frozenset({"google"}), 42),
        ]
        self.assertEqual([r.node_id for r in rank_candidates(results, ["google"])], ["known", "unknown"])
        self.assertEqual(rank_candidates(results, []), [])


if __name__ == "__main__":
    unittest.main()
