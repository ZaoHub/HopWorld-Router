import json
import subprocess
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ENCODER = r'''
const path = require('path');
const root = process.cwd();
const qrcode = require(path.join(root, 'router/static/qrcode.js'));
global.qrcode = qrcode;
const about = require(path.join(root, 'router/static/v1-about.js'));
const results = about.SUPPORT_CHANNELS.map(channel => {
  const encoded = about.encodeQr(channel.payload, 4, 2);
  if (!encoded) throw new Error('missing QR for ' + channel.id);
  return {
    id: channel.id,
    payload: encoded.payload,
    expected: channel.payload,
    moduleCount: encoded.moduleCount,
    modules: encoded.modules,
  };
});
process.stdout.write(JSON.stringify(results));
'''


def rasterize(modules, cell=10, margin=4):
    count = len(modules)
    size = (count + margin * 2) * cell
    image = np.full((size, size), 255, dtype=np.uint8)
    for row, line in enumerate(modules):
        for col, dark in enumerate(line):
            if not dark:
                continue
            y = (row + margin) * cell
            x = (col + margin) * cell
            image[y:y + cell, x:x + cell] = 0
    return image


class AboutQrRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        encoded = subprocess.check_output(
            ['node', '-e', ENCODER],
            cwd=ROOT,
            text=True,
        )
        cls.payloads = json.loads(encoded)

    def test_generated_qrcodes_decode_to_exact_payloads(self):
        detector = cv2.QRCodeDetector()
        self.assertEqual(len(self.payloads), 3)
        for item in self.payloads:
            with self.subTest(id=item['id']):
                self.assertEqual(item['payload'], item['expected'])
                image = rasterize(item['modules'])
                decoded, _, _ = detector.detectAndDecode(image)
                self.assertEqual(decoded, item['expected'])
                self.assertFalse(item['payload'].startswith('ethereum:'))


if __name__ == '__main__':
    unittest.main()
