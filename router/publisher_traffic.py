"""Aggregate native publisher byte counters without mixing legacy counters."""

from collections import deque
from datetime import date, timedelta
import sqlite3
import threading
import time

from router.traffic_history import FIVE_MINUTES, LIVE_INTERVAL, LIVE_POINTS, STALE_SECONDS, _bytes


class PublisherTraffic:
    def __init__(self, connect):
        self.connect = connect
        self._lock = threading.Lock()
        self._rates = deque(maxlen=LIVE_POINTS)
        self._live = {}
        with connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS publisher_traffic_checkpoint (
                    publisher TEXT PRIMARY KEY, epoch TEXT NOT NULL, sampled_at REAL NOT NULL,
                    up INTEGER NOT NULL, down INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS publisher_traffic (
                    bucket INTEGER NOT NULL, day TEXT NOT NULL, publisher TEXT NOT NULL,
                    core TEXT NOT NULL, protocol TEXT NOT NULL, port INTEGER NOT NULL,
                    up INTEGER NOT NULL, down INTEGER NOT NULL, estimated INTEGER NOT NULL,
                    PRIMARY KEY (bucket, publisher, protocol, port));
                CREATE INDEX IF NOT EXISTS publisher_traffic_day ON publisher_traffic(day);
            """)

    def sample(self, publishers, now=None):
        now = time.time() if now is None else now
        day = date.fromtimestamp(now).isoformat()
        bucket = int(now) // FIVE_MINUTES * FIVE_MINUTES
        tracked = 0
        rates = []
        with self._lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for publisher, core, protocol, port, reading in publishers:
                if reading is None:
                    continue
                epoch, up, down = reading
                if not epoch or any(type(n) is not int or not 0 <= n < 2**63 for n in (up, down)):
                    continue
                tracked += 1
                previous = db.execute(
                    'SELECT epoch, sampled_at, up, down FROM publisher_traffic_checkpoint WHERE publisher=?', (publisher,),
                ).fetchone()
                if previous:
                    old_epoch, old_at, old_up, old_down = previous
                    same = epoch == old_epoch and up >= old_up and down >= old_down
                    delta_up = up - old_up if same else up
                    delta_down = down - old_down if same else down
                    elapsed = now - old_at
                    fresh = same and 0 < elapsed <= STALE_SECONDS
                    if fresh:
                        rates.append((round(delta_up * 8 / elapsed), round(delta_down * 8 / elapsed)))
                    estimated = int(bool(delta_up or delta_down) and
                                    (not fresh or int(old_at) // FIVE_MINUTES != bucket // FIVE_MINUTES))
                else:
                    delta_up = delta_down = estimated = 0
                db.execute('INSERT OR IGNORE INTO publisher_traffic VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0)',
                           (bucket, day, publisher, core, protocol, port))
                if delta_up or delta_down:
                    db.execute('UPDATE publisher_traffic SET up = up + ?, down = down + ?, estimated = MAX(estimated, ?) '
                               'WHERE bucket=? AND publisher=? AND protocol=? AND port=?',
                               (delta_up, delta_down, estimated, bucket, publisher, protocol, port))
                db.execute('INSERT OR REPLACE INTO publisher_traffic_checkpoint VALUES (?, ?, ?, ?, ?)',
                           (publisher, epoch, now, up, down))
            up_bps, down_bps = (map(sum, zip(*rates)) if tracked and len(rates) == tracked else (None, None))
            live_bucket = int(now) // LIVE_INTERVAL * LIVE_INTERVAL
            point = (live_bucket, up_bps, down_bps)
            if self._rates and self._rates[-1][0] == live_bucket:
                self._rates[-1] = point
            else:
                self._rates.append(point)
            self._live = {'sampled_at': now, 'up_bps': up_bps, 'down_bps': down_bps,
                          'tracked_publishers': tracked, 'active_publishers': len(publishers),
                          'state': 'ok' if tracked else 'unavailable' if publishers else 'inactive'}

    def daily_range(self, start, end):
        try:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            if first.isoformat() != start or last.isoformat() != end:
                raise ValueError
        except ValueError as exc:
            raise ValueError('请选择有效的起止日期') from exc
        days = (last - first).days
        if not 0 <= days < 366:
            raise ValueError('日期范围需为 1 至 366 天')
        return self._daily([(first + timedelta(days=i)).isoformat() for i in range(days + 1)])

    def _daily(self, dates):
        with self.connect() as db:
            counts = {row[0]: _bytes(*row[1:]) for row in db.execute('''
                SELECT day, SUM(up), SUM(down), MAX(estimated) FROM publisher_traffic
                WHERE day BETWEEN ? AND ? GROUP BY day''', (dates[0], dates[-1]))}
        return [{'date': day, **counts.get(day, _bytes())} for day in dates]

    def snapshot(self, now=None, *, dashboard=False):
        now = time.time() if now is None else now
        with self._lock:
            live = dict(self._live)
            rates = {point[0]: point[1:] for point in self._rates} if dashboard else {}
        day = date.fromtimestamp(now)
        dates = [(day - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
        month = day.replace(day=1).isoformat()
        end_hour = int(now) // 3600 * 3600
        end_bucket = int(now) // FIVE_MINUTES * FIVE_MINUTES
        totals = 'SUM(up), SUM(down), MAX(estimated)'
        with self.connect() as db:
            db.execute('BEGIN')
            daily = {row[0]: _bytes(*row[1:]) for row in db.execute(
                f'SELECT day, {totals} FROM publisher_traffic WHERE day BETWEEN ? AND ? GROUP BY day', (min(month, dates[0]), dates[-1]))}
            hourly = {row[0]: _bytes(*row[1:]) for row in db.execute(
                f'SELECT bucket / 3600 * 3600, {totals} FROM publisher_traffic WHERE bucket BETWEEN ? AND ? GROUP BY 1',
                (min(end_hour - 23 * 3600, int(time.mktime(day.timetuple())) // 3600 * 3600), end_bucket))}
            by_core = {row[0]: _bytes(*row[1:]) for row in db.execute(
                f'SELECT core, {totals} FROM publisher_traffic WHERE day=? GROUP BY core', (dates[-1],))}
            breakdown = [{'publisher': row[0], 'core': row[1], 'protocol': row[2], 'port': row[3], **_bytes(*row[4:])}
                         for row in db.execute(f'SELECT publisher, core, protocol, port, {totals} FROM publisher_traffic '
                                               'WHERE day=? GROUP BY publisher, core, protocol, port', (dates[-1],))]
            cumulative = _bytes(*db.execute(f'SELECT {totals} FROM publisher_traffic').fetchone())
            month_total = _bytes(*db.execute(f'SELECT {totals} FROM publisher_traffic WHERE day BETWEEN ? AND ?', (month, dates[-1])).fetchone())
            recent = {row[0]: _bytes(*row[1:]) for row in db.execute(
                f'SELECT bucket, {totals} FROM publisher_traffic WHERE bucket BETWEEN ? AND ? GROUP BY bucket',
                (end_bucket - 11 * FIVE_MINUTES, end_bucket))} if dashboard else {}
        view = {'state': 'unavailable', 'sampled_at': None, 'up_bps': None, 'down_bps': None,
                **live, 'today': daily.get(dates[-1], _bytes()), 'month': month_total, 'cumulative': cumulative,
                'by_core': by_core, 'publishers': breakdown,
                'hourly': [{'timestamp': hour, **hourly.get(hour, _bytes())} for hour in range(end_hour - 23 * 3600, end_hour + 1, 3600)],
                'today_hourly': [{'timestamp': hour, **hourly.get(hour, _bytes())}
                                 for hour in range(end_hour - 24 * 3600, end_hour + 1, 3600) if date.fromtimestamp(hour) == day],
                'month_daily': [{'date': day.replace(day=i).isoformat(), **daily.get(day.replace(day=i).isoformat(), _bytes())}
                                for i in range(1, day.day + 1)],
                'daily': [{'date': key, **daily.get(key, _bytes())} for key in dates[-7:]]}
        if live.get('sampled_at') is not None and now - live['sampled_at'] > STALE_SECONDS:
            view.update(state='unavailable', up_bps=None, down_bps=None)
        if dashboard:
            live_end = int(now) // LIVE_INTERVAL * LIVE_INTERVAL
            view.update(last_30_daily=[{'date': key, **daily.get(key, _bytes())} for key in dates],
                        live=[{'timestamp': stamp, **_bytes(*rates.get(stamp, (None, None)))}
                              for stamp in range(live_end - (LIVE_POINTS - 1) * LIVE_INTERVAL, live_end + 1, LIVE_INTERVAL)],
                        recent=[{'timestamp': stamp, **recent.get(stamp, _bytes())}
                                for stamp in range(end_bucket - 11 * FIVE_MINUTES, end_bucket + 1, FIVE_MINUTES)])
        return view


def collect(history, read_publishers, stopped, interval):
    while not stopped.is_set():
        try:
            history.sample(read_publishers())
        except sqlite3.Error:
            pass
        if stopped.wait(interval):
            return
