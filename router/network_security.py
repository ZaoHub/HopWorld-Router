"""Manage per-publisher DNS policies and report the evidence available for each revision."""

import json
import re
import sqlite3
import sys
import threading
import time
import uuid

from router.independent_core import PUBLISHERS, publisher_bind, publisher_core
from router.gateway_preflight import gateway_requirements
from router.security_dns import SING_BOX_VERSION, validate_resolver
from router.security_probe import observe_publisher

API = '/api/v1/security/'
ID = r'[a-z0-9][a-z0-9-]{0,39}'
SCOPE = 'proxy_business'
CLIENT_LIMIT = '浏览器本机 DNS、独立加密 DNS 和 WebRTC 需要在受管客户端上实测。'


class UnsupportedPolicy(ValueError):
    pass


class NetworkSecurity:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS dns_resolvers (
                    id TEXT PRIMARY KEY, config TEXT NOT NULL, created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS security_policies (
                    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL UNIQUE, mode TEXT NOT NULL,
                    upstream_id TEXT NOT NULL, resolver_id TEXT NOT NULL,
                    desired_revision INTEGER NOT NULL, active_revision INTEGER, verified_revision INTEGER,
                    active_config TEXT, previous_config TEXT, updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS security_test_runs (
                    id TEXT PRIMARY KEY, policy_id TEXT NOT NULL, profile_id TEXT NOT NULL,
                    policy_revision INTEGER, action TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL,
                    started_at INTEGER NOT NULL, finished_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS security_findings (
                    test_run_id TEXT NOT NULL, code TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL,
                    PRIMARY KEY (test_run_id, code)
                );
                CREATE TABLE IF NOT EXISTS security_events (
                    id INTEGER PRIMARY KEY, policy_id TEXT, instance_id TEXT, action TEXT NOT NULL,
                    result TEXT NOT NULL, revision INTEGER, timestamp INTEGER NOT NULL
                );
            """)
            interrupted = list(db.execute(
                "SELECT id,policy_id,profile_id,policy_revision,action FROM security_test_runs "
                "WHERE status IN ('PENDING','RUNNING')"
            ))
            for job in interrupted:
                db.execute('UPDATE security_test_runs SET status=?,detail=?,finished_at=? WHERE id=?',
                           ('INCONCLUSIVE', 'Router 重启，作业中断', int(time.time()), job['id']))
                self._event(db, job['policy_id'], job['profile_id'], job['action'], 'INCONCLUSIVE',
                            job['policy_revision'])

    @staticmethod
    def capabilities():
        platforms = {'linux': 'CONFIGURABLE', 'darwin': 'CONFIGURABLE'}
        compatibility = platforms.get(sys.platform, 'UNSUPPORTED')
        linux_tools, tun = gateway_requirements() if sys.platform == 'linux' else ({}, False)
        missing = [tool for tool, available in linux_tools.items() if not available]
        if linux_tools and not tun:
            missing.append('/dev/net/tun')
        return {
            'platform': sys.platform, 'scope': SCOPE, 'sing_box_version': SING_BOX_VERSION,
            'linux_tools': linux_tools,
            'modes': {'compatibility': compatibility, 'rules': 'UNSUPPORTED', 'strict': 'UNSUPPORTED'},
            'strict_reason': '严格发布需通过 Linux 出口隔离与独立网络验收。',
            'gateway': 'UNSUPPORTED', 'gateway_cli': 'PREFLIGHT_REQUIRED' if linux_tools and not missing else 'UNSUPPORTED',
            'gateway_missing_requirements': missing,
            'webrtc': 'UNSUPPORTED',
            'client_limit': CLIENT_LIMIT, 'control_plane_exceptions': [],
            'cores': {
                core: {'fixed_configuration': fixed or None,
                       'tcp': 'INCONCLUSIVE' if fixed else 'UNSUPPORTED',
                       'udp': 'INCONCLUSIVE' if fixed else 'UNSUPPORTED',
                       'dns': compatibility if core == 'sing-box' else 'UNSUPPORTED',
                       'strict': 'UNSUPPORTED'}
                for core, (_, _, _, _, _, fixed) in PUBLISHERS.items()
            },
        }

    @staticmethod
    def _event(db, policy_id, profile_id, action, result, revision=None):
        db.execute('INSERT INTO security_events(policy_id,instance_id,action,result,revision,timestamp) VALUES (?,?,?,?,?,?)',
                   (policy_id, profile_id, action, result, revision, int(time.time())))

    def add_resolver(self, data):
        config = validate_resolver(data)
        identifier = uuid.uuid4().hex
        with self.store.connect() as db:
            db.execute('INSERT INTO dns_resolvers VALUES (?,?,?)', (identifier, json.dumps(config), int(time.time())))
            self._event(db, None, None, 'resolver.create', 'PASS')
        return {'id': identifier, **config, 'route_via': 'selected_upstream', 'tls_verify': True}

    def resolvers(self):
        with self.store.connect() as db:
            return [{'id': row['id'], **json.loads(row['config']), 'route_via': 'selected_upstream', 'tls_verify': True}
                    for row in db.execute('SELECT id,config FROM dns_resolvers ORDER BY created_at,id')]

    def save_policy(self, identifier, data):
        if set(data) != {'mode', 'profile_id', 'upstream_id', 'resolver_id'}:
            raise ValueError('策略字段无效')
        mode, profile_id, upstream_id, resolver_id = (data[key] for key in ('mode', 'profile_id', 'upstream_id', 'resolver_id'))
        if (mode not in ('strict', 'rules', 'compatibility') or not isinstance(profile_id, str)
                or not isinstance(upstream_id, str) or not re.fullmatch(r'[0-9a-f]{64}', upstream_id)
                or not isinstance(resolver_id, str) or not re.fullmatch(r'[0-9a-f]{32}', resolver_id)):
            raise ValueError('请选择安全模式、发布实例、上游和解析器')
        with self.store._local_lock, self.store.connect() as db:
            if profile_id not in self.store.independent_profiles():
                raise ValueError('请选择已有的独立发布实例')
            if not db.execute('SELECT 1 FROM dns_resolvers WHERE id=?', (resolver_id,)).fetchone():
                raise ValueError('请选择已保存的解析器')
            row = db.execute('SELECT * FROM security_policies WHERE id=?', (identifier,)).fetchone()
            owner = db.execute('SELECT id FROM security_policies WHERE profile_id=?', (profile_id,)).fetchone()
            if (row and row['profile_id'] != profile_id) or (owner and owner['id'] != identifier):
                raise ValueError('一个发布实例只能绑定一条安全策略')
            if row and all(row[key] == value for key, value in data.items()):
                return self._public_policy(row)
            revision = row['desired_revision'] + 1 if row else 1
            db.execute(
                'INSERT INTO security_policies(id,profile_id,mode,upstream_id,resolver_id,desired_revision,updated_at) '
                'VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET mode=excluded.mode,upstream_id=excluded.upstream_id,'
                'resolver_id=excluded.resolver_id,desired_revision=excluded.desired_revision,updated_at=excluded.updated_at',
                (identifier, profile_id, mode, upstream_id, resolver_id, revision, int(time.time())),
            )
            self._event(db, identifier, profile_id, 'policy.save', 'PASS', revision)
            return self._public_policy(db.execute('SELECT * FROM security_policies WHERE id=?', (identifier,)).fetchone())

    @staticmethod
    def _public_policy(row):
        fields = ('id', 'profile_id', 'mode', 'upstream_id', 'resolver_id', 'desired_revision', 'active_revision',
                  'verified_revision', 'updated_at')
        active = json.loads(row['active_config']) if row['active_config'] else None
        return ({key: row[key] for key in fields} | {'scope': SCOPE, 'active': active,
                                                    'can_rollback': row['active_revision'] is not None})

    def _policy(self, identifier, db):
        row = db.execute('SELECT * FROM security_policies WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('安全策略不存在')
        return row

    def binding(self, profile_id):
        with self.store.connect() as db:
            row = db.execute('SELECT active_config FROM security_policies WHERE profile_id=?', (profile_id,)).fetchone()
        return json.loads(row['active_config']) if row and row['active_config'] else None

    def publisher_status(self, profile_id):
        binding = self.binding(profile_id)
        runtime = self.store.publisher_runtime(profile_id) if binding else None
        return {
            'dns': ('ACTIVE_UNVERIFIED' if runtime.security_revision() == binding['revision'] else
                    'CONFIGURED_NOT_APPLIED') if runtime else 'NOT_CONFIGURED',
            'strict': 'UNSUPPORTED', 'client': 'INCONCLUSIVE',
        }

    def status(self):
        with self.store._local_lock, self.store.connect() as db:
            rows = db.execute('SELECT * FROM security_policies ORDER BY profile_id').fetchall()
            profiles = self.store.independent_profiles()
            report = []
            for row in rows:
                policy = self._public_policy(row)
                profile = profiles.get(row['profile_id'], {})
                if policy['active']:
                    running = profile.get('enabled') and self.store.publisher_runtime(
                        row['profile_id']).security_revision() == row['active_revision']
                    runtime = ('ACTIVE_UNVERIFIED' if running else
                               'PROTECTION_FAILED' if profile.get('enabled') else 'CONFIGURED_NOT_APPLIED')
                else:
                    runtime = 'NOT_CONFIGURED'
                pending = row['active_revision'] != row['desired_revision']
                state = ('UNSUPPORTED' if row['mode'] != 'compatibility' or publisher_core(row['profile_id']) != 'sing-box'
                         else 'CONFIGURED_NOT_APPLIED' if pending else runtime)
                last = db.execute(
                    "SELECT id FROM security_test_runs WHERE policy_id=? AND policy_revision=? AND action='test' "
                    "AND finished_at IS NOT NULL ORDER BY rowid DESC LIMIT 1", (row['id'], row['active_revision'])
                ).fetchone()
                report.append({**policy, 'status': state, 'runtime_status': runtime, 'last_test': last['id'] if last else None})
            publishers = [{'id': key, 'core_id': publisher_core(key), 'egress': profile.get('egress'),
                           'upstream_id': profile.get('upstream_id'), 'port': profile.get('port'),
                           'enabled': bool(profile.get('enabled'))} for key, profile in profiles.items()]
            return {
                'scope': SCOPE, 'policies': report, 'publishers': publishers, 'status': 'NOT_CONFIGURED' if not rows else None,
                'kill_switch': 'UNSUPPORTED', 'client_limit': CLIENT_LIMIT,
            }

    def _prepare(self, row, db):
        if row['mode'] != 'compatibility':
            raise UnsupportedPolicy('当前仅能应用独立 sing-box 的兼容模式。严格模式和分流模式尚缺少实测。')
        profile_id = row['profile_id']
        if publisher_core(profile_id) != 'sing-box':
            raise UnsupportedPolicy('当前只有独立 sing-box 支持应用加密 DNS 策略')
        if sys.platform not in ('linux', 'darwin'):
            raise UnsupportedPolicy('当前平台尚未适配独立 sing-box 的 DNS 策略')
        profile = self.store.independent_profiles().get(profile_id, {})
        if profile.get('egress') != 'fixed' or profile.get('upstream_id') != row['upstream_id']:
            raise ValueError('发布实例必须绑定到策略指定的固定 SOCKS5 上游')
        resolver = db.execute('SELECT config FROM dns_resolvers WHERE id=?', (row['resolver_id'],)).fetchone()
        if not resolver:
            raise ValueError('策略指定的解析器不存在')
        return profile, {'revision': row['desired_revision'], 'upstream_id': row['upstream_id'],
                         'resolver_id': row['resolver_id'], 'resolver': json.loads(resolver['config'])}

    def validate(self, identifier):
        with self.store._local_lock, self.store.connect() as db:
            row = self._policy(identifier, db)
            result, detail = 'PASS', '内核接受配置，待验证 TCP、UDP、DNS 和 IPv4/IPv6 出口。'
            try:
                profile, binding = self._prepare(row, db)
                self.store.validate_security_profile(row['profile_id'], profile, binding)
            except UnsupportedPolicy as exc:
                result, detail = 'UNSUPPORTED', str(exc)
            except (OSError, ValueError) as exc:
                result, detail = 'FAIL', str(exc)
            self._event(db, identifier, row['profile_id'], 'validate', result, row['desired_revision'])
            return {'status': result, 'scope': SCOPE, 'revision': row['desired_revision'], 'detail': detail}

    def _activate(self, row, db, action):
        profile_id = row['profile_id']
        previous = row['active_config']
        if action == 'apply':
            profile, binding = self._prepare(row, db)
            if not profile.get('enabled'):
                raise ValueError('请先启动此发布实例')
            if row['active_revision'] == row['desired_revision']:
                # Re-applying also reconciles any process that exited since the last apply.
                previous = row['previous_config']
        else:
            if not row['active_config']:
                raise ValueError('此策略尚无已应用版本')
            profile = self.store.independent_profiles().get(profile_id, {})
            binding = json.loads(row['previous_config']) if row['previous_config'] else None
            previous = None
        db.execute(
            'UPDATE security_policies SET active_config=?,previous_config=?,active_revision=?,verified_revision=NULL WHERE id=?',
            (json.dumps(binding) if binding else None, previous, binding['revision'] if binding else None, row['id']),
        )
        if profile.get('enabled'):
            self.store.apply_security_profile(profile_id, profile, binding)
        return '已应用兼容模式 DNS 配置，出口路径待实测。' if binding else '已解除此发布实例的 DNS 策略。'

    def start_job(self, identifier, action):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._policy(identifier, db)
            if db.execute("SELECT 1 FROM security_test_runs WHERE status IN ('PENDING','RUNNING')").fetchone():
                raise ValueError('已有网络安全作业正在运行')
            job_id = uuid.uuid4().hex
            revision = row['active_revision'] if action == 'test' else row['desired_revision']
            db.execute('INSERT INTO security_test_runs VALUES (?,?,?,?,?,?,?,?,?)',
                       (job_id, identifier, row['profile_id'], revision, action, 'PENDING', '', int(time.time()), None))
        threading.Thread(target=self._run_job, args=(job_id, identifier, revision, action), daemon=True).start()
        return {'id': job_id, 'status': 'PENDING'}

    def _run_job(self, job_id, identifier, revision, action):
        findings = []
        try:
            with self.store.connect() as db:
                db.execute('UPDATE security_test_runs SET status=? WHERE id=?', ('RUNNING', job_id))
            if action == 'test':
                with self.store.connect() as db:
                    row = self._policy(identifier, db)
                status, detail, findings = self._diagnose(row)
            else:
                profile_changed = None
                with self.store._local_lock:
                    try:
                        with self.store.connect() as db:
                            row = self._policy(identifier, db)
                            if row['desired_revision'] != revision:
                                raise ValueError('策略版本已变化，请重试')
                            detail = self._activate(row, db, action)
                            profile_changed = row['profile_id']
                    except sqlite3.Error:
                        if profile_changed:
                            self.store.publisher_runtime(profile_changed).stop()
                        raise
                status = 'PASS'
        except UnsupportedPolicy as exc:
            status, detail = 'UNSUPPORTED', str(exc)
        except (OSError, ValueError) as exc:
            status, detail = 'FAIL', str(exc)
        except Exception:
            status, detail = 'FAIL', '安全作业未完成，请检查数据库和内核运行状态'
        with self.store.connect() as db:
            db.execute('UPDATE security_test_runs SET status=?,detail=?,finished_at=? WHERE id=?',
                       (status, detail, int(time.time()), job_id))
            db.executemany('INSERT INTO security_findings VALUES (?,?,?,?)',
                           [(job_id, *finding) for finding in findings])
            job = db.execute('SELECT profile_id FROM security_test_runs WHERE id=?', (job_id,)).fetchone()
            self._event(db, identifier, job['profile_id'], action, status, revision)

    def _diagnose(self, row):
        if not row['active_config']:
            raise UnsupportedPolicy('请先应用已支持的 DNS 策略')
        profile_id = row['profile_id']
        binding = json.loads(row['active_config'])
        runtime = self.store.publisher_runtime(profile_id)
        if runtime.security_revision() != binding['revision']:
            raise ValueError('内核未运行已应用版本，请检查发布实例状态')
        profile = self.store.independent_profiles().get(profile_id)
        links = runtime.links()
        if not profile or not links or not runtime.binary:
            raise ValueError('发布入口未就绪，请检查监听和证书')
        observations = observe_publisher(runtime.binary, links[0], publisher_bind(profile))
        if runtime.security_revision() != binding['revision'] or runtime.links() != links:
            return 'INCONCLUSIVE', '诊断期间发布实例发生变化，请重测。', []
        findings = [
            ('DNS_CONFIG', 'PASS', '发布进程正在使用指定版本的 DNS 配置'),
            *observations,
            ('UDP', 'INCONCLUSIVE', '需要发起 UDP 回显并验证 SOCKS5 中继及 WAN 路径'),
            ('WEBRTC', 'UNSUPPORTED', CLIENT_LIMIT),
            ('KILL_SWITCH', 'UNSUPPORTED', '当前发布实例未使用 Linux 隔离出口'),
        ]
        if any(status == 'FAIL' for _, status, _ in observations):
            return 'FAIL', '发布入口的 DNS 诊断失败。', findings
        return 'INCONCLUSIVE', '已取得发布入口观测结果，出口路径等待独立网络证据。', findings

    def job(self, identifier):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM security_test_runs WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise ValueError('安全作业不存在')
            findings = [dict(item) for item in db.execute(
                'SELECT code,status,detail FROM security_findings WHERE test_run_id=? ORDER BY rowid', (identifier,))]
        return {**dict(row), 'scope': SCOPE, 'platform': sys.platform, 'core': publisher_core(row['profile_id']),
                'core_version': SING_BOX_VERSION if row['action'] == 'test' and row['policy_revision'] is not None else None,
                'findings': findings}

    def get(self, path):
        if path == 'capabilities':
            return self.capabilities()
        if path == 'status':
            return self.status()
        if path == 'policies':
            with self.store.connect() as db:
                return {'policies': [self._public_policy(row) for row in db.execute(
                    'SELECT * FROM security_policies ORDER BY profile_id')]}
        if path == 'dns/resolvers':
            return {'resolvers': self.resolvers()}
        if path == 'events':
            with self.store.connect() as db:
                return {'events': [dict(row) for row in db.execute('SELECT * FROM security_events ORDER BY id DESC LIMIT 100')]}
        if match := re.fullmatch(r'tests/([a-f0-9]{32})', path):
            return self.job(match[1])
        return None

    def write(self, method, path, data):
        if method == 'PUT' and (match := re.fullmatch(rf'policies/({ID})', path)):
            return 200, self.save_policy(match[1], data)
        if method != 'POST':
            return None
        if path == 'dns/resolvers':
            return 201, self.add_resolver(data)
        if path == 'tests':
            identifier = data.get('policy_id')
            if set(data) != {'policy_id'} or not isinstance(identifier, str) or not re.fullmatch(ID, identifier):
                raise ValueError('请选择安全策略')
            return 202, self.start_job(identifier, 'test')
        if match := re.fullmatch(rf'policies/({ID})/(validate|apply|rollback)', path):
            if data:
                raise ValueError('此操作不需要参数')
            identifier, action = match.groups()
            if action == 'validate':
                return 200, self.validate(identifier)
            return 202, self.start_job(identifier, action)
        return None
