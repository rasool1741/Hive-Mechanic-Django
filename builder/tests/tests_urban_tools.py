import time
import unittest

from ..urban_tools import create_qr_token, inside_radius, validate_location_sample, verify_qr_token


class UrbanToolValidationTests(unittest.TestCase):
    def test_location_and_radius(self):
        self.assertEqual([], validate_location_sample(35.7, 51.4, 10))
        self.assertTrue(inside_radius(35.7, 51.4, 35.7, 51.4, 10))
        self.assertTrue(validate_location_sample(95, 51, 10))

    def test_signed_qr_token(self):
        token = create_qr_token('game', 'stage', 'nonce', 'secret', time.time() + 60)
        self.assertEqual('game', verify_qr_token(token, 'secret')['game'])
        self.assertIsNone(verify_qr_token(token, 'wrong-secret'))
