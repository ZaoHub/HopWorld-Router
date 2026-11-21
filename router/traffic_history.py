"""Persist deltas from the local Xray business traffic counters."""

from collections import deque
from datetime import date, timedelta
import sqlite3
import threading
import time


SAMPLE_SECONDS = 2
STALE_SECONDS = 3 * SAMPLE_SECONDS
FIVE_MINUTES = 300
LIVE_INTERVAL = 10
LIVE_POINTS = 180
SCHEMA = """
CREATE TABLE IF NOT EXISTS traffic_checkpoint (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    epoch TEXT NOT NULL,
    up INTEGER NOT NULL,
    down INTEGER NOT NULL,
    sampled_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS traffic_hours (
    hour INTEGER NOT NULL,
    day TEXT NOT NULL,
    up INTEGER NOT NULL,
    down INTEGER NOT NULL,
    estimated INTEGER NOT NULL,
    PRIMARY KEY (hour, day)
);
CREATE INDEX IF NOT EXISTS traffic_hours_day ON traffic_hours(day);
CREATE TABLE IF NOT EXISTS traffic_recent (
    bucket INTEGER PRIMARY KEY,
    up INTEGER NOT NULL,
    down INTEGER NOT NULL,
    estimated INTEGER NOT NULL
);
"""


def _bytes(up=None, down=None, estimated=False):
    return {
        'up_bytes': up,
        'down_bytes': down,
        'total_bytes': up + down if up is not None else None,
        'estimated': bool(estimated),
    }


class TrafficHistory:
    def __init__(self, connect):
        self.connect = connect
        self._lock = threading.Lock()
        self._live = {'state': 'unavailable', 'sampled_at': None}
        self._rates = deque(maxlen=LIVE_POINTS)
        with connect() as db:
            db.executescript(SCHEMA)

    def sample(self, counters, now=None):
        now = time.time() if now is None else now
        state = counters['state']
        snapshot = {'state': state, 'sampled_at': now}
        with self._lock:
            if state == 'ok':
                up, down, epoch = (counters.get(key) for key in ('up_bytes', 'down_bytes', 'instance'))
                if not epoch or any(type(value) is not int or not 0 <= value < 2**63 for value in (up, down)):
                    snapshot['state'] = 'unavailable'
                else:
                    snapshot.update(_bytes(up, down), instance=epoch, up_bps=None, down_bps=None)
                    self._record(snapshot)
            self._live = snapshot
            bucket = int(now) // LIVE_INTERVAL * LIVE_INTERVAL
            point = (bucket, snapshot.get('up_bps'), snapshot.get('down_bps'))
            if self._rates and self._rates[-1][0] == bucket:
                self._rates[-1] = point
            else:
                self._rates.append(point)

    def _record(self, sample):
        now = sample['sampled_at']
        up, down, epoch = sample['up_bytes'], sample['down_bytes'], sample['instance']
        hour = int(now) // 3600 * 3600
        bucket = int(now) // FIVE_MINUTES * FIVE_MINUTES
        day = date.fromtimestamp(now).isoformat()

        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT epoch, up, down, sampled_at FROM traffic_checkpoint WHERE id = 1').fetchone()
            delta_up = delta_down = estimated = 0
            reset = False
            if previous:
                old_epoch, old_up, old_down, old_at = previous
                stable = epoch == old_epoch and up >= old_up and down >= old_down
                if stable:
                    delta_up, delta_down = up - old_up, down - old_down
                    last = self._live
                    last_matches = (last.get('instance'), last.get('up_bytes'), last.get('down_bytes')) == (old_epoch, old_up, old_down)
                    measured_at = last['sampled_at'] if last_matches else old_at
                    elapsed = now - measured_at
                    if 0 < elapsed <= STALE_SECONDS and self._live['state'] == 'ok':
                        sample['up_bps'] = round(delta_up * 8 / elapsed)
                        sample['down_bps'] = round(delta_down * 8 / elapsed)
                    estimated = int(elapsed > STALE_SECONDS or elapsed <= 0 or date.fromtimestamp(measured_at).isoformat() != day
                                    or int(measured_at) // 3600 * 3600 != hour)
                else:
                    reset = True
                    delta_up, delta_down, estimated = up, down, 1
            if not (delta_up or delta_down):
                estimated = 0
            new_hour = db.execute(
                'INSERT OR IGNORE INTO traffic_hours VALUES (?, ?, 0, 0, 0)', (hour, day),
            ).rowcount
            if delta_up or delta_down:
                db.execute(
                    'UPDATE traffic_hours SET up = up + ?, down = down + ?, estimated = MAX(estimated, ?) WHERE hour = ? AND day = ?',
                    (delta_up, delta_down, estimated, hour, day),
                )
            new_bucket = not previous or int(previous[3]) // FIVE_MINUTES != bucket // FIVE_MINUTES
            if new_bucket or delta_up or delta_down or self._live['state'] != 'ok':
                db.execute('INSERT OR IGNORE INTO traffic_recent VALUES (?, 0, 0, 0)', (bucket,))
                if delta_up or delta_down:
                    db.execute(
                        'UPDATE traffic_recent SET up = up + ?, down = down + ?, estimated = MAX(estimated, ?) WHERE bucket = ?',
                        (delta_up, delta_down, int(bool(new_bucket or estimated)), bucket),
                    )
            if new_bucket:
                db.execute('DELETE FROM traffic_recent WHERE bucket < ?', (bucket - 12 * FIVE_MINUTES,))
            if new_hour or new_bucket or delta_up or delta_down or reset or now - previous[3] >= 30:
                db.execute('INSERT OR REPLACE INTO traffic_checkpoint VALUES (1, ?, ?, ?, ?)', (epoch, up, down, now))

    def snapshot(self, now=None, *, dashboard=False):
        now = time.time() if now is None else now
        with self._lock:
            live = dict(self._live)
            rates = {point[0]: point[1:] for point in self._rates} if dashboard else {}
        if live['sampled_at'] is None or now - live['sampled_at'] > STALE_SECONDS:
            live = {'state': 'unavailable', 'sampled_at': live['sampled_at']}
        today = date.fromtimestamp(now)
        end_hour = int(now) // 3600 * 3600
        first_hour = end_hour - 23 * 3600
        dates = [(today - timedelta(days=i)).isoformat() for i in range(29 if dashboard else 6, -1, -1)]
        month = today.replace(day=1).isoformat()
        first_day = min(month, dates[0])
        month_dates = [today.replace(day=day).isoformat() for day in range(1, today.day + 1)]
        totals = 'SUM(up), SUM(down), MAX(estimated)'
        with self.connect() as db:
            db.execute('BEGIN')
            hourly = {row[0]: _bytes(*row[1:]) for row in db.execute(
                f'SELECT hour, {totals} FROM traffic_hours WHERE hour BETWEEN ? AND ? GROUP BY hour',
                (first_hour, end_hour),
            )}
            daily = {row[0]: _bytes(*row[1:]) for row in db.execute(
                f'SELECT day, {totals} FROM traffic_hours WHERE day BETWEEN ? AND ? GROUP BY day',
                (first_day, dates[-1]),
            )}
            month_total = _bytes(*db.execute(
                f'SELECT {totals} FROM traffic_hours WHERE day BETWEEN ? AND ?', (month, dates[-1]),
            ).fetchone())
            cumulative = _bytes(*db.execute(f'SELECT {totals} FROM traffic_hours').fetchone())
            if dashboard:
                end_bucket = int(now) // FIVE_MINUTES * FIVE_MINUTES
                recent = {row[0]: _bytes(*row[1:]) for row in db.execute(
                    'SELECT bucket, up, down, estimated FROM traffic_recent WHERE bucket BETWEEN ? AND ?',
                    (end_bucket - 11 * FIVE_MINUTES, end_bucket),
                )}
        view = {
            **_bytes(), 'instance': None, 'up_bps': None, 'down_bps': None, **live,
            'today': daily.get(dates[-1], _bytes()), 'month': month_total, 'cumulative': cumulative,
            'hourly': [{'timestamp': hour, **hourly.get(hour, _bytes())} for hour in range(first_hour, end_hour + 1, 3600)],
            'today_hourly': [{'timestamp': hour, **hourly.get(hour, _bytes())} for hour in range(first_hour, end_hour + 1, 3600) if date.fromtimestamp(hour) == today],
            'month_daily': [{'date': day, **daily.get(day, _bytes())} for day in month_dates],
            'daily': [{'date': day, **daily.get(day, _bytes())} for day in dates[-7:]],
        }
        if dashboard:
            end_live = int(now) // LIVE_INTERVAL * LIVE_INTERVAL
            view.update({
                'live': [{'timestamp': stamp, **_bytes(*rates.get(stamp, (None, None)))}
                         for stamp in range(end_live - (LIVE_POINTS - 1) * LIVE_INTERVAL, end_live + LIVE_INTERVAL, LIVE_INTERVAL)],
                'recent': [{'timestamp': stamp, **recent.get(stamp, _bytes())}
                           for stamp in range(end_bucket - 11 * FIVE_MINUTES, end_bucket + FIVE_MINUTES, FIVE_MINUTES)],
                'last_30_daily': [{'date': day, **daily.get(day, _bytes())} for day in dates],
            })
        return view

    def daily_range(self, start, end):
        try:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            if first.isoformat() != start or last.isoformat() != end:
                raise ValueError
        except ValueError as exc:
            raise ValueError('请选择有效的起止日期') from exc
        days = (last - first).days
        if days < 0 or days >= 366:
            raise ValueError('日期范围需为 1 至 366 天')
        with self.connect() as db:
            counts = {row[0]: _bytes(*row[1:]) for row in db.execute(
                'SELECT day, SUM(up), SUM(down), MAX(estimated) FROM traffic_hours WHERE day BETWEEN ? AND ? GROUP BY day',
                (start, end),
            )}
        dates = [(first + timedelta(days=offset)).isoformat() for offset in range(days + 1)]
        return [{'date': day, **counts.get(day, _bytes())} for day in dates]


def collect(history, read_counters, stopped):
    while not stopped.is_set():
        try:
            history.sample(read_counters())
        except sqlite3.Error:
            history.sample({'state': 'unavailable'})
        if stopped.wait(SAMPLE_SECONDS):
            return
