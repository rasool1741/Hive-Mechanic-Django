"""Server-side validation helpers for urban interaction cards."""

import hashlib
import hmac
import json
import time


def validate_location_sample(latitude, longitude, accuracy, maximum_age_seconds=120, timestamp=None):
    errors = []
    if not -90 <= float(latitude) <= 90:
        errors.append('latitude خارج از بازه معتبر است.')
    if not -180 <= float(longitude) <= 180:
        errors.append('longitude خارج از بازه معتبر است.')
    if float(accuracy) < 0:
        errors.append('accuracy نمی‌تواند منفی باشد.')
    if timestamp is not None and time.time() - float(timestamp) > maximum_age_seconds:
        errors.append('نمونه مکانی قدیمی است.')
    return errors


def inside_radius(latitude, longitude, target_latitude, target_longitude, radius_meters):
    from math import asin, cos, radians, sin, sqrt
    earth_radius = 6371000
    lat1, lat2 = radians(float(latitude)), radians(float(target_latitude))
    delta_lat = radians(float(target_latitude) - float(latitude))
    delta_lon = radians(float(target_longitude) - float(longitude))
    value = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    distance = earth_radius * 2 * asin(sqrt(value))
    return distance <= float(radius_meters)


def create_qr_token(game_slug, stage_id, nonce, secret, expires_at):
    payload = {'game': game_slug, 'stage': stage_id, 'nonce': nonce, 'expires_at': int(expires_at)}
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    signature = hmac.new(secret.encode('utf-8'), encoded.encode('utf-8'), hashlib.sha256).hexdigest()
    return '%s.%s' % (encoded, signature)


def verify_qr_token(token, secret, now=None):
    try:
        encoded, signature = token.rsplit('.', 1)
        expected = hmac.new(secret.encode('utf-8'), encoded.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(encoded)
        if (now or time.time()) > payload['expires_at']:
            return None
        return payload
    except (AttributeError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
