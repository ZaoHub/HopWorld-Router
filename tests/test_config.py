import unittest

from router.config import build_proxy_config


class ConfigTests(unittest.TestCase):
    def test_proxy_config_is_isolated_and_does_not_mutate_input(self):
        outbound = {"protocol": "vless", "tag": "old", "settings": {"vnext": []}}
        config = build_proxy_config(outbound)
        self.assertEqual(outbound["tag"], "old")
        self.assertEqual(config["outbounds"][0]["tag"], "proxy")
        self.assertEqual([item["listen"] for item in config["inbounds"]], ["127.0.0.1"] * 2)
        self.assertEqual(config["routing"]["rules"][0]["outboundTag"], "direct")
        self.assertEqual(config["routing"]["rules"][1]["outboundTag"], "proxy")

    def test_rejects_invalid_ports_and_non_proxy_outbounds(self):
        for ports in ((0, 10), (80, 80), (True, 10), (1, 65536)):
            with self.subTest(ports=ports), self.assertRaises(ValueError):
                build_proxy_config({"protocol": "vless"}, *ports)
        for outbound in ({}, {"protocol": "freedom"}, {"protocol": "blackhole"}):
            with self.subTest(outbound=outbound), self.assertRaises(ValueError):
                build_proxy_config(outbound)


if __name__ == "__main__":
    unittest.main()
