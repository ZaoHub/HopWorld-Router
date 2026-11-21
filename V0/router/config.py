"""Build the isolated Xray configuration used by the router service."""

from copy import deepcopy


def build_proxy_config(outbound: dict, http_port: int = 17890, socks_port: int = 17891) -> dict:
    """Use an already validated Xray outbound; bind both listeners to loopback."""
    if not isinstance(outbound, dict) or not isinstance(outbound.get("protocol"), str):
        raise ValueError("An Xray outbound with a protocol is required")
    if not outbound["protocol"] or outbound["protocol"] in {"freedom", "blackhole"}:
        raise ValueError("A proxy outbound is required")
    if any(type(port) is not int or not 1 <= port <= 65535 for port in (http_port, socks_port)):
        raise ValueError("Ports must be between 1 and 65535")
    if http_port == socks_port:
        raise ValueError("HTTP and SOCKS ports must differ")

    proxy = deepcopy(outbound)
    proxy["tag"] = "proxy"
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {"tag": "http-in", "listen": "127.0.0.1", "port": http_port,
             "protocol": "http", "settings": {},
             "sniffing": {"enabled": True, "routeOnly": True, "destOverride": ["http", "tls"]}},
            {"tag": "socks-in", "listen": "127.0.0.1", "port": socks_port,
             "protocol": "socks", "settings": {"udp": True, "auth": "noauth"},
             "sniffing": {"enabled": True, "routeOnly": True, "destOverride": ["http", "tls", "quic"]}},
        ],
        "outbounds": [proxy, {"protocol": "freedom", "tag": "direct", "settings": {}}],
        "routing": {"domainStrategy": "IPIfNonMatch", "rules": [
            {"type": "field", "outboundTag": "direct", "ip": ["geoip:private"]},
            {"type": "field", "outboundTag": "proxy", "network": "tcp,udp"},
        ]},
    }
