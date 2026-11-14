"""Certbot manual-auth-hook / manual-cleanup-hook for DNS-01."""

import os
import sys
import time

from router.acme_dns import clear_acme_txt, publish_acme_txt


def _data_dir():
    return os.environ.get('HOPWORLD_DATA', './data').strip() or './data'


def main(argv=None):
    argv = argv or sys.argv[1:]
    if len(argv) < 1 or argv[0] not in ('auth', 'cleanup'):
        raise SystemExit('usage: acme_dns_hook auth|cleanup')
    domain = os.environ.get('CERTBOT_DOMAIN', '').strip()
    if not domain:
        raise SystemExit('CERTBOT_DOMAIN missing')
    data_dir = _data_dir()
    if argv[0] == 'auth':
        validation = os.environ.get('CERTBOT_VALIDATION', '').strip()
        if not validation:
            raise SystemExit('CERTBOT_VALIDATION missing')
        publish_acme_txt(data_dir, domain, validation)
        time.sleep(int(os.environ.get('HOPWORLD_ACME_DNS_WAIT', '25')))
        return
    clear_acme_txt(data_dir, domain)


if __name__ == '__main__':
    main()
