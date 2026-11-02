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


# --- Graph answer tools (Quiz / Text) -------------------------------------------------
#
# Contract for conditional graph edges. The player never supplies a variable. A Quiz
# ("answer") or Text ("exact") tool lists the accepted answers (a value or a list of
# values). When the server validates the player's answer it stores the AUTHORED accepted
# value (not the raw text the player typed) in session_state['graph_vars'] under the tool's
# variable name. Edge conditions {"variable": ..., "equals": ...} only read graph_vars.

ANSWER_TOOL_KINDS = {'quiz': 'quiz', 'text': 'text', 'text_input': 'text'}


def answer_tool_kind(tool):
    """Return 'quiz' or 'text' for an answer-bearing tool, else None."""
    return ANSWER_TOOL_KINDS.get(tool.get('type')) if isinstance(tool, dict) else None


def accepted_answers(settings_data, kind):
    """Authored accepted answers of a quiz ('answer') or text ('exact') tool.

    Returns None when the tool declares none (free-form tool), a list of strings when it
    does, and an empty list when the declaration is invalid (empty list or unsupported
    types), which can never match.
    """
    key = 'answer' if kind == 'quiz' else 'exact'
    if not isinstance(settings_data, dict) or key not in settings_data:
        return None
    value = settings_data[key]
    items = value if isinstance(value, list) else [value]
    if not items or any(isinstance(item, bool) or not isinstance(item, (str, int, float)) for item in items):
        return []
    return [str(item) for item in items]


def answer_key(kind, authored):
    """Form in which two authored accepted answers count as duplicates."""
    return str(authored).strip().casefold() if kind == 'quiz' else str(authored)


def match_answer(kind, submitted, accepted):
    """Return the authored accepted value that equals what the player sent, else None.

    Quiz: ignores case and outer spaces. Text: only the player's outer spaces are ignored
    and the comparison is case-sensitive (unchanged from the previous behaviour).
    """
    if kind == 'quiz':
        normalized = str(submitted).strip().casefold()
        return next((candidate for candidate in accepted if candidate.strip().casefold() == normalized), None)
    return next((candidate for candidate in accepted if str(submitted).strip() == candidate), None)


def tool_variable_name(node_id, tool, kind):
    """Variable an answer tool writes to: settings.variable, else '<node id>.<quiz|text>'."""
    name = (tool.get('settings') or {}).get('variable') if isinstance(tool.get('settings'), dict) else None
    if isinstance(name, str) and name.strip():
        return name.strip()
    return '%s.%s' % (node_id, kind)


# --- One contract for edge conditions, shared by publish validation and the runtime ----

CONDITION_NONE, CONDITION_VALID, CONDITION_INVALID = 'none', 'valid', 'invalid'


def parse_condition(condition):
    """Parse an edge's ``condition`` value. Returns (status, variable, equals).

    * ``None`` (or no condition at all) is the ONLY way to say "no condition".
    * A valid condition is a dict with ``equals`` and exactly one of ``variable`` / ``key``
      naming a non-empty string, and no other keys.
    * Everything else is invalid and must never be treated as "always true": ``{}``,
      ``[]``, ``''``, ``0``, ``False``, other types, both ``variable`` and ``key``,
      unknown keys, an empty or non-string variable name, a missing ``equals``.
    """
    if condition is None:
        return CONDITION_NONE, None, None
    if not isinstance(condition, dict) or set(condition) - {'variable', 'key', 'equals'}:
        return CONDITION_INVALID, None, None
    if 'equals' not in condition or ('variable' in condition) == ('key' in condition):
        return CONDITION_INVALID, None, None
    variable = condition['variable'] if 'variable' in condition else condition['key']
    if not isinstance(variable, str) or not variable:
        return CONDITION_INVALID, None, None
    return CONDITION_VALID, variable, condition['equals']


def graph_dominators(node_ids, edges, start):
    """Dominator sets of the nodes reachable from ``start``: {node: set of dominating nodes}.

    A node D dominates N when every path from ``start`` to N passes through D (a node
    dominates itself). Plain iterative algorithm; it also terminates on graphs with cycles.
    """
    successors = {node_id: set() for node_id in node_ids}
    predecessors = {node_id: set() for node_id in node_ids}
    for edge in edges:
        if isinstance(edge, dict) and edge.get('source') in successors and edge.get('target') in successors:
            successors[edge['source']].add(edge['target'])
            predecessors[edge['target']].add(edge['source'])
    if start not in successors:
        return {}

    reachable, stack = set(), [start]
    while stack:
        current = stack.pop()
        if current not in reachable:
            reachable.add(current)
            stack.extend(successors[current])

    dominators = {node_id: set(reachable) for node_id in reachable}
    dominators[start] = {start}
    changed = True
    while changed:
        changed = False
        for node_id in reachable:
            if node_id == start:
                continue
            parents = [dominators[parent] for parent in predecessors[node_id] if parent in reachable]
            updated = {node_id} | (set.intersection(*parents) if parents else set())
            if updated != dominators[node_id]:
                dominators[node_id] = updated
                changed = True
    return dominators
