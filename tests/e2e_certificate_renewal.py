"""Exercise Router and a real Hysteria 2 node across renewal and manual import.

Set HOPWORLD_HYSTERIA and HOPWORLD_SINGBOX to the two binaries. Certbot is
simulated with temporary, real certificate pairs and no external DNS writes.
"""

from contextlib import ExitStack
import os
from pathlib import Path
import socket
import subprocess
from tempfile import TemporaryDirectory
import threading
from unittest.mock import patch

from router.acme import materialize_certs
from router.server import Store
from tests.e2e_independent_cores import DOMAIN, echo, free_port, listener, round_trip, start_client


def main():
    for name in ('HOPWORLD_HYSTERIA', 'HOPWORLD_SINGBOX'):
        if not os.environ.get(name):
            raise SystemExit(f'Set {name} to the binary path')
    with TemporaryDirectory() as temporary, ExitStack() as stack:
        root = Path(temporary)
        live, renewed, imported = root / 'live', root / 'renewed', root / 'imported'
        for directory, days in ((live, '1'), (renewed, '30'), (imported, '60')):
            directory.mkdir()
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', days,
                            '-subj', f'/CN={DOMAIN}', '-addext', f'subjectAltName=DNS:{DOMAIN}',
                            '-out', str(directory / 'fullchain.pem'), '-keyout', str(directory / 'privkey.pem')],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stack.enter_context(patch('router.acme.letsencrypt_live', return_value=live))
        stack.enter_context(patch('router.acme._certbot_path', return_value='certbot'))
        stack.enter_context(patch('router.acme._renew_days', return_value=3))
        cert, key = materialize_certs(root, DOMAIN)
        store = Store(root / 'router.db')
        stack.enter_context(patch.object(store, 'require_domain_binding'))
        store.register_domain(DOMAIN, 'ipv4')
        store.save_acme_settings({**store.acme_settings(), 'domain': DOMAIN, 'bound_domain': DOMAIN,
                                  'enabled': True, 'email': 'fixture@example.test', 'challenge': 'http',
                                  'cert_path': str(cert), 'key_path': str(key)})
        target = listener(stack)
        target_port = target.getsockname()[1]
        udp = listener(stack, socket.SOCK_DGRAM, target_port)
        for sock in (target, udp):
            threading.Thread(target=echo, args=(sock,), daemon=True).start()
        taken = {target_port}
        core = store.independent['hysteria2']
        stack.callback(core.stop)
        store.start_independent_core('hysteria2', {
            'public_host': DOMAIN, 'port': free_port(taken), 'public_listener': False,
            'egress': 'direct', 'upstream_id': '',
        })
        node_id = store.nodes()[0]['id']
        binaries = {'sing-box': os.environ['HOPWORLD_SINGBOX']}
        initial_pid = core._read_pid()
        with ExitStack() as client:
            port = free_port(taken)
            start_client(client, root, 'hysteria2', store.node_link(node_id), target_port, port, cert, binaries)
            round_trip(port)
            with patch('router.acme._run_certbot', side_effect=lambda *_:
                       (live / cert.name).write_bytes((renewed / cert.name).read_bytes())):
                assert not store.sync_acme_certificate()
            assert core._read_pid() == initial_pid
            assert store.acme_status()['certificate_valid']
            assert store.acme_status()['history'][0]['action'] == '自动续签检查'
            round_trip(port)
        with patch('router.acme._run_certbot', side_effect=lambda *_:
                   (live / key.name).write_bytes((renewed / key.name).read_bytes())):
            assert store.sync_acme_certificate()
        assert core._read_pid() != initial_pid
        assert cert.read_bytes() == (renewed / cert.name).read_bytes()
        assert store.acme_status()['history'][0]['detail'] == '有效证书已同步'
        with ExitStack() as client:
            port = free_port(taken)
            start_client(client, root, 'hysteria2', store.node_link(node_id), target_port, port, cert, binaries)
            round_trip(port)
        print('Hysteria 2 retained TCP/UDP on the old certificate after failed renewal and used the new certificate after retry')
        renewed_pid = core._read_pid()
        from_files = (imported / 'fullchain.pem', imported / 'privkey.pem')
        result = store.import_manual_certificate(DOMAIN, *(file.read_text() for file in from_files))
        assert result['certificate_valid'] and not result['automatic']
        assert core._read_pid() != renewed_pid
        assert cert.read_bytes() == (renewed / cert.name).read_bytes()
        with ExitStack() as client:
            port = free_port(taken)
            start_client(client, root, 'hysteria2', store.node_link(node_id, local_only=True),
                         target_port, port, from_files[0], binaries)
            round_trip(port)
        print('Hysteria 2 used the imported certificate and completed TCP/UDP round trips using the exported node')


if __name__ == '__main__':
    main()
