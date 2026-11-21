import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from router.acme import (_run_certbot, cert_paths, days_until_expiry, ensure_ip_certificate, ip_certificate_paths,
                         materialize_certs, renew_certificate, stage_certificate, sync_certificate, validate_acme_email)


class AcmeTests(unittest.TestCase):
    def test_materialize_retains_cached_pair_when_certbot_lineage_is_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            domain = 'router.example.com'
            cert, key = cert_paths(data, domain)
            cert.parent.mkdir(parents=True)
            cert.write_bytes(b'cached cert')
            key.write_bytes(b'cached key')
            with patch('router.acme.letsencrypt_live', return_value=Mock(is_dir=Mock(side_effect=PermissionError))):
                self.assertEqual(materialize_certs(data, domain), (cert, key))

    def test_ip_certificate_reports_connection_failure_from_certbot_stdout(self):
        command = ['certbot', 'certonly', '--ip-address', '8.8.8.8']
        error = subprocess.CalledProcessError(
            1, command, output='Additional context\n' * 40 + 'Detail: Timeout during connect (likely firewall problem)',
            stderr='Some challenges have failed.',
        )
        with patch('router.acme.subprocess.run', side_effect=error):
            with self.assertRaisesRegex(ValueError, '无法连接公网 IP 的 TCP 80'):
                _run_certbot(command, Path('.'))

    def test_ip_certificate_distinguishes_the_outbound_acme_api_timeout(self):
        command = ['certbot', 'certonly', '--ip-address', '8.8.8.8']
        error = subprocess.CalledProcessError(
            1, command, stderr="HTTPSConnectionPool(host='acme-v02.api.letsencrypt.org', port=443): Read timed out.",
        )
        with patch('router.acme.subprocess.run', side_effect=error):
            with self.assertRaisesRegex(ValueError, 'HTTPS 接口超时（出站 TCP 443）'):
                _run_certbot(command, Path('.'))

    def test_validate_email(self):
        self.assertEqual(validate_acme_email('  a@b.co  '), 'a@b.co')
        with self.assertRaises(ValueError):
            validate_acme_email('not-an-email')

    def test_domain_preflight_uses_the_certonly_subcommand(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict('os.environ', {'HOPWORLD_ACME_WEBROOT': temporary,
                                       'HOPWORLD_ACME_WRAPPER': 'sudo /usr/bin/certbot'}), \
             patch('router.acme._certbot_path', return_value='/usr/bin/certbot'), \
             patch('router.acme.subprocess.run') as run:
            self.assertEqual(stage_certificate(temporary, 'node.example.com', 'owner@example.test', 'http'), 'http')
            command = run.call_args.args[0]
            self.assertEqual(command[:3], ['sudo', '/usr/bin/certbot', 'certonly'])
            self.assertIn('--dry-run', command)

    def test_ipv6_preflight_selects_staging_and_never_materializes_a_certificate(self):
        address = '2606:4700::1111'
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict('os.environ', {'HOPWORLD_ACME_TLS_PORT': '10443'}), \
             patch('router.acme._lego_runtime', return_value='lego'), \
             patch('router.acme.subprocess.run') as run, patch('router.acme._sync_live_certificate') as publish:
            self.assertEqual(stage_certificate(temporary, address, 'owner@example.test', 'tls-alpn'), 'tls-alpn')
            command = run.call_args.args[0]
            self.assertEqual(command[command.index('--tls.address') + 1], '[::]:10443')
            self.assertEqual(command[command.index('--server') + 1], 'letsencrypt-staging')
            self.assertEqual(command[command.index('--profile') + 1], 'shortlived')
            publish.assert_not_called()
            with self.assertRaisesRegex(ValueError, '公网 IP'):
                stage_certificate(temporary, address, 'owner@example.test', 'dns')

    @patch('router.acme._certbot_path', return_value='certbot')
    def test_forced_renewal_requires_a_new_certificate(self, _certbot):
        domain = 'node.example.com'
        with tempfile.TemporaryDirectory() as tmp:
            cert, key = cert_paths(tmp, domain)
            cert.parent.mkdir(parents=True)
            cert.write_bytes(b'old cert')
            key.write_bytes(b'key')
            with patch('router.acme._run_certbot'):
                with self.assertRaisesRegex(ValueError, '尚未同步'):
                    renew_certificate(tmp, domain, 'a@b.co', 'http', force=True)
            with patch('router.acme.validate_key_pair'), \
                 patch('router.acme._run_certbot', side_effect=lambda *_: cert.write_bytes(b'new cert')) as certbot:
                self.assertEqual(renew_certificate(tmp, domain, 'a@b.co', 'http', force=True), (cert, key))
            self.assertIn('--force-renewal', certbot.call_args.args[0])

    @patch('router.acme.subprocess.run')
    def test_days_until_expiry(self, mock_run):
        mock_run.return_value.stdout = 'notAfter=Dec 31 23:59:59 2026 GMT\n'
        mock_run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as tmp:
            cert = Path(tmp) / 'cert.pem'
            cert.write_text('dummy', encoding='utf-8')
            days = days_until_expiry(cert)
            self.assertIsNotNone(days)

    def test_automatic_renewal_reads_new_live_pair_and_preserves_old_pair_on_mismatch(self):
        domain = 'node.example.com'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cert, key = cert_paths(root, domain)
            live = root / 'live'
            live.mkdir()
            helpers = root / 'next'
            helpers.mkdir()
            for location, days in ((live, '1'), (helpers, '30')):
                subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', days,
                                '-subj', f'/CN={domain}', '-out', str(location / cert.name),
                                '-keyout', str(location / key.name)],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            initial_cert = (live / cert.name).read_bytes()
            initial_key = (live / key.name).read_bytes()
            renewed_cert = (helpers / cert.name).read_bytes()
            renewed_key = (helpers / key.name).read_bytes()

            with patch('router.acme.letsencrypt_live', return_value=live), \
                 patch('router.acme._certbot_path', return_value='certbot'), \
                 patch('router.acme._renew_days', return_value=3):
                def certbot_without_key(*_):
                    (live / cert.name).write_bytes(renewed_cert)

                with patch('router.acme._run_certbot', side_effect=certbot_without_key):
                    with self.assertRaisesRegex(ValueError, '尚未同步'):
                        sync_certificate(root, domain, 'a@b.co', True, 'http')
                self.assertEqual((cert.read_bytes(), key.read_bytes()), (initial_cert, initial_key))

                with patch('router.acme._run_certbot', side_effect=lambda *_: (live / key.name).write_bytes(renewed_key)) as run:
                    result = sync_certificate(root, domain, 'a@b.co', True, 'http')
                self.assertIn('--force-renewal', run.call_args.args[0])
                self.assertEqual((cert.read_bytes(), key.read_bytes()), (renewed_cert, renewed_key))
                self.assertGreater(result['days_left'], 20)
                self.assertEqual(key.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(shutil.which('openssl'), 'OpenSSL required for ACME fixture')
    def test_ip_issuance_and_renewal_validate_the_ipv4_and_ipv6_san(self):
        def openssl(*args):
            subprocess.run(['openssl', *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ca, ca_key = root / 'ca.pem', root / 'ca.key'
            openssl('req', '-new', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '7',
                    '-subj', '/CN=Router test CA', '-out', str(ca), '-keyout', str(ca_key))
            for address, webroot in (('8.8.8.8', ''), ('2606:4700::1111', str(root / 'www'))):
                with self.subTest(address=address):
                    certificate, key = map(Path, ip_certificate_paths(root, address))
                    live = root / 'ip-acme' / 'config' / 'live' / certificate.parent.name
                    live.mkdir(parents=True)
                    csr = root / 'request.pem'
                    extension = root / 'extensions.cnf'
                    openssl('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=Router test IP',
                            '-out', str(csr), '-keyout', str(live / key.name))

                    def issue(san, days):
                        extension.write_text(f'basicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:{san}\n')
                        openssl('x509', '-req', '-in', str(csr), '-CA', str(ca), '-CAkey', str(ca_key), '-CAcreateserial',
                                '-extfile', str(extension), '-days', str(days), '-out', str(live / certificate.name))

                    with patch.dict('os.environ', {'SSL_CERT_FILE': str(ca), 'HOPWORLD_ACME_WEBROOT': webroot,
                                                   'HOPWORLD_ACME_WRAPPER': ''}), \
                         patch('router.acme._certbot_path', return_value='certbot'), \
                         patch('router.acme.subprocess.check_output', return_value='certbot 5.4.0'):
                        with patch('router.acme._run_certbot', side_effect=lambda *_: issue(address, 1)) as run:
                            expires = ensure_ip_certificate(root, address, 'a@b.co')
                        command = run.call_args.args[0]
                        self.assertEqual(command[command.index('--ip-address') + 1], address)
                        self.assertEqual(command[command.index('--required-profile') + 1], 'shortlived')
                        self.assertIn('--webroot' if webroot else '--standalone', command)
                        original = certificate.read_bytes()
                        with patch('router.acme._run_certbot', side_effect=lambda *_: issue('1.1.1.1', 6)):
                            with self.assertRaisesRegex(ValueError, '未同步有效'):
                                ensure_ip_certificate(root, address, 'a@b.co', renew=True)
                        self.assertEqual(certificate.read_bytes(), original)
                        with patch('router.acme._run_certbot', side_effect=lambda *_: issue(address, 6)) as run:
                            renewed = ensure_ip_certificate(root, address, 'a@b.co', renew=True)
                        self.assertIn('--force-renewal', run.call_args.args[0])
                        self.assertGreater(renewed, expires + 4 * 86400)


if __name__ == '__main__':
    unittest.main()
