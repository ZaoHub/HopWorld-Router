import base64
import unittest

from router.subscription import combine_subscriptions, decode_subscription, encode_subscription


class SubscriptionTests(unittest.TestCase):
    def test_multiple_links_round_trip(self):
        links = ["vless://example#香港", "trojan://example#东京"]
        self.assertEqual(base64.b64decode(encode_subscription(links)).decode(), "\n".join(links))

    def test_rejects_empty_and_multiline_inputs(self):
        for links in ([], [""], ["vless://a\ntrojan://b"], [" vless://a"], ["invalid"]):
            with self.subTest(links=links), self.assertRaises(ValueError):
                encode_subscription(links)

    def test_decode_plain_and_base64_then_deduplicate(self):
        first = "vless://example#香港"
        second = "trojan://example#东京"
        raw = f"# subscription\n{first}\n{second}\n".encode()
        self.assertEqual(decode_subscription(raw), [first, second])
        encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
        self.assertEqual(decode_subscription(encoded), [first, second])
        self.assertEqual(combine_subscriptions([[first, second], [first]]), [first, second])

    def test_rejects_unknown_formats_and_oversized_data(self):
        for content in (b"proxies:\n  - name: example", b"unknown://example", b"a" * (2 * 1024 * 1024 + 1)):
            with self.subTest(content=content[:30]), self.assertRaises(ValueError):
                decode_subscription(content)


if __name__ == "__main__":
    unittest.main()
