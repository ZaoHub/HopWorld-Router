from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from router.server import Store


class TrafficHistoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / 'router.db'
        self.history = Store(self.db).traffic_history
        self.now = datetime(2025, 7, 17, 14).timestamp()

    def record(self, up, down, offset=0, epoch='a'):
        self.history.sample({'state': 'ok', 'up_bytes': up, 'down_bytes': down, 'instance': epoch}, self.now + offset)

    def test_counter_deltas_persist_across_manager_and_core_restarts(self):
        self.record(100, 150)
        self.record(150, 250, 2)
        before_restart = self.history.snapshot(self.now + 2)
        self.assertEqual((before_restart['up_bps'], before_restart['down_bps']), (200, 400))
        self.assertEqual(before_restart['cumulative']['total_bytes'], 150)

        self.history = Store(self.db).traffic_history
        self.record(180, 300, 4)
        after_restart = self.history.snapshot(self.now + 4)
        self.assertIsNone(after_restart['up_bps'])
        self.assertEqual(after_restart['cumulative']['total_bytes'], 230)
        self.record(12, 8, 6, epoch='b')
        self.record(12, 8, 8, epoch='b')
        self.record(20, 10, 10, epoch='b')
        after_reset = self.history.snapshot(self.now + 10)
        self.assertEqual(after_reset['cumulative']['total_bytes'], 260)
        self.assertEqual((after_reset['up_bps'], after_reset['down_bps']), (32, 8))
        self.assertTrue(after_reset['today']['estimated'])

    def test_calendar_rollover_preserves_history_and_unknown_buckets(self):
        midnight = datetime.fromtimestamp(self.now).replace(hour=0) + timedelta(days=1)
        self.now = midnight.timestamp() - 4
        self.record(0, 0)
        self.record(30, 40, 2)
        self.record(80, 90, 6)
        state = self.history.snapshot(self.now + 6)
        self.assertEqual(len(state['hourly']), 24)
        self.assertIsNone(state['hourly'][-3]['total_bytes'])
        self.assertEqual(len(state['daily']), 7)
        self.assertEqual(state['daily'][-2]['total_bytes'], 70)
        self.assertEqual(state['today']['total_bytes'], 100)
        self.assertEqual(state['month']['total_bytes'], 170)
        self.assertEqual(state['daily'][-1]['date'], midnight.date().isoformat())

    def test_metrics_error_does_not_record_fake_zero_rates(self):
        self.record(0, 0)
        self.history.sample({'state': 'unavailable'}, self.now + 2)
        self.assertIsNone(self.history.snapshot(self.now + 2)['up_bps'])
        self.record(20, 30, 30)
        snapshot = self.history.snapshot(self.now + 30)
        self.assertEqual(snapshot['state'], 'ok')
        self.assertIsNone(snapshot['up_bps'])
        self.assertTrue(snapshot['hourly'][-1]['estimated'])
        self.assertEqual(snapshot['cumulative']['total_bytes'], 50)
        self.assertEqual(self.history.snapshot(self.now + 60)['state'], 'unavailable')

    def test_dashboard_includes_five_minute_buckets_and_live_samples_with_gaps(self):
        self.record(0, 0)
        self.record(2, 4, 2)
        self.record(4, 8, 300)
        self.record(7, 14, 302)
        snapshot = self.history.snapshot(self.now + 302, dashboard=True)
        self.assertEqual(len(snapshot['recent']), 12)
        self.assertIsNone(snapshot['recent'][-3]['total_bytes'])
        self.assertEqual(snapshot['recent'][-2]['total_bytes'], 6)
        self.assertEqual(snapshot['recent'][-1]['total_bytes'], 15)
        self.assertTrue(snapshot['recent'][-1]['estimated'])
        self.assertEqual(snapshot['live'][-1]['total_bytes'], 36)
        self.assertIsNone(snapshot['live'][-2]['total_bytes'])
        self.assertEqual(len(snapshot['last_30_daily']), 30)
        self.assertEqual(len(snapshot['daily']), 7)
        self.assertNotIn('live', self.history.snapshot(self.now + 302))

    def test_custom_range_reuses_archived_totals_and_validates_boundaries(self):
        self.record(0, 0)
        self.record(100, 50, 2)
        results = self.history.daily_range('2025-07-16', '2025-07-18')
        self.assertEqual([row['total_bytes'] for row in results], [None, 150, None])
        for start, end in [('2025-07-18', '2025-07-16'), ('20250716', '2025-07-18'), ('2024-01-01', '2025-07-18')]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.history.daily_range(start, end)

    def test_independent_traffic_tracks_instances_and_keeps_legacy_history_separate(self):
        history = Store(self.db).publisher_traffic
        def publish(hy, ss, offset, epoch='a'):
            history.sample([
                ('hysteria2', 'hysteria2', 'hysteria2', 8442, (epoch, *hy)),
                ('mihomo', 'mihomo', 'ss', 8388, ('b', *ss)),
                ('tuic-server', 'tuic-server', 'tuic', 8446, None),
            ], self.now + offset)
        self.record(0, 0)
        self.record(500, 700, 2)
        publish((1, 1), (1, 1), 0)
        publish((21, 41), (11, 21), 2)
        totals = history.snapshot(self.now + 2, dashboard=True)
        self.assertEqual((totals['tracked_publishers'], totals['active_publishers']), (2, 3))
        self.assertEqual((totals['up_bps'], totals['down_bps']), (120, 240))
        self.assertEqual(totals['today']['total_bytes'], 90)
        self.assertEqual(totals['by_core']['mihomo']['total_bytes'], 30)
        self.assertEqual(history.daily_range('2025-07-17', '2025-07-17')[0]['total_bytes'], 90)
        self.assertEqual(self.history.snapshot(self.now + 2)['today']['total_bytes'], 1200)
        history = Store(self.db).publisher_traffic
        publish((2, 3), (11, 21), 4, epoch='new')
        totals = history.snapshot(self.now + 4)
        self.assertEqual(totals['today']['total_bytes'], 95)
        self.assertTrue(totals['today']['estimated'])
        self.assertIsNone(totals['up_bps'])
