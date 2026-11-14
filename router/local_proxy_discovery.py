"""Check common loopback proxies by tunneling a random marker to a local socket."""
import secrets
import socket
import threading

COMMON_PORTS = (1080, 10808, 10809, 3128, 7890, 7891, 7897, 8080, 8118, 8888)


def _read(sock, size):
    data = b''
    while len(data) < size:
        part = sock.recv(size - len(data))
        if not part:
            raise OSError('closed')
        data += part
    return data


def _check(port, protocol, timeout):
    marker = secrets.token_bytes(24)
    matched = threading.Event()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(timeout)
        target = listener.getsockname()[1]

        def accept():
            try:
                conn, _ = listener.accept()
                with conn:
                    conn.settimeout(timeout)
                    if _read(conn, len(marker)) == marker:
                        matched.set()
            except OSError:
                pass

        threading.Thread(target=accept, daemon=True).start()
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=timeout) as conn:
                conn.settimeout(timeout)
                if protocol == 'socks':
                    conn.sendall(b'\x05\x01\x00')
                    if _read(conn, 2) != b'\x05\x00': return False
                    conn.sendall(b'\x05\x01\x00\x01\x7f\x00\x00\x01' + target.to_bytes(2, 'big'))
                    head = _read(conn, 4)
                    if head[:2] != b'\x05\x00': return False
                    size = {1: 4, 4: 16}.get(head[3])
                    if head[3] == 3: size = _read(conn, 1)[0]
                    if size is None: return False
                    _read(conn, size + 2)
                else:
                    conn.sendall(f'CONNECT 127.0.0.1:{target} HTTP/1.1\r\nHost: 127.0.0.1:{target}\r\n\r\n'.encode())
                    head = b''
                    while b'\r\n\r\n' not in head and len(head) < 8192: head += _read(conn, 1)
                    if head.split(b'\r\n', 1)[0].split()[:2] not in ([b'HTTP/1.0', b'200'], [b'HTTP/1.1', b'200']): return False
                conn.sendall(marker)
                return matched.wait(timeout)
        except OSError:
            return False


def discover(ports=COMMON_PORTS, timeout=0.3):
    if not isinstance(ports, (list, tuple)) or len(ports) > 20 or any(type(p) is not int or not 1 <= p <= 65535 for p in ports):
        raise ValueError('端口列表无效')
    result = []
    for port in dict.fromkeys(ports):
        for protocol in ('socks', 'http'):
            if _check(port, protocol, timeout):
                result.append({'protocol': protocol, 'port': port})
                break
    return result
