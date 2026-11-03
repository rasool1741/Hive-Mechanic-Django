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
    accepted = [str(item) for item in items]
    if kind == 'text':
        # The runtime compares the player's text after strip() and rejects text longer than
        # max_length before comparing. A Text answer that is empty, has outer spaces or does
        # not fit could therefore never be entered: it is unusable, not just unusual.
        maximum = text_max_length(settings_data)  # None = invalid limit: reported separately, not judged here
        if any(not item.strip() or item != item.strip() or (maximum is not None and len(item) > maximum)
               for item in accepted):
            return []
    return accepted


DEFAULT_TEXT_MAX_LENGTH = 1000


def text_max_length(settings_data):
    """The limit the runtime applies to Text input, or None when ``max_length`` is invalid.

    The default (1000) applies ONLY when ``max_length`` is absent or null. A valid limit is a
    whole number of at least 1: an int, an integral float (50.0) or a string of ASCII digits
    ('50'). Everything else is invalid and gives None, it is never replaced by the default:
    0, negatives, '', whitespace, 'abc', '5.5', 5.7, true/false, lists and objects.
    Publishing and the runtime both call this one function.
    """
    if settings_data is None:
        settings_data = {}
    if not isinstance(settings_data, dict):
        return None
    value = settings_data.get('max_length')
    if value is None:
        return DEFAULT_TEXT_MAX_LENGTH
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, int):
            number = value
        elif isinstance(value, float):
            number = int(value) if value.is_integer() else None
        elif isinstance(value, str):
            stripped = value.strip()
            number = int(stripped) if stripped.isascii() and stripped.isdigit() else None
        else:
            number = None
    except (ValueError, OverflowError):
        number = None
    return number if number is not None and number >= 1 else None


def answer_key(kind, authored):
    """Form in which two authored accepted answers count as duplicates."""
    return str(authored).strip().casefold() if kind == 'quiz' else str(authored)


def match_answer(kind, submitted, accepted):
    """Return the authored accepted value that equals what the player sent, else None.

    Quiz: ignores case and outer spaces; the stored (canonical) value is the authored one.
    Text: the player's outer spaces are ignored and the comparison is case-sensitive and exact,
    so the canonical value is identical to the player's stripped text. Empty text never matches.
    """
    if kind == 'quiz':
        normalized = str(submitted).strip().casefold()
        return next((candidate for candidate in accepted if candidate.strip().casefold() == normalized), None)
    text = str(submitted).strip()
    if not text:
        return None
    return next((candidate for candidate in accepted if candidate == text), None)


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


# --- Structure of ONE branching node, shared by publish validation and the runtime -----

BRANCH_MIXED, BRANCH_INVALID_CONDITION, BRANCH_MULTIPLE_VARIABLES = 'mixed', 'invalid-condition', 'multiple-variables'
BRANCH_DUPLICATE_CONDITION = 'duplicate-condition'


def _condition_key(variable, equals):
    """Hashable identity of a valid condition; 'key' and 'variable' spellings are the same condition."""
    try:
        return variable, json.dumps(equals, sort_keys=True)
    except (TypeError, ValueError):
        return variable, repr(equals)


def branch_problems(outgoing_edges):
    """Structural problems of the outgoing edges of ONE node, as a list of (code, detail).

    A node branches when at least one outgoing edge has a condition other than None. Such a
    node must have only conditional edges, every condition valid, exactly one variable, and no
    two edges with the same condition (that would make the path ambiguous whenever it applies,
    so the whole node is refused even when only one of the twins happens to match).
    Publishing and the runtime both apply exactly these rules, so a graph published earlier
    (or edited by hand) cannot make the runtime pick a path the rules forbid.
    """
    parsed = [(edge, parse_condition(edge.get('condition'))) for edge in outgoing_edges]
    conditional = [(edge, result) for edge, result in parsed if result[0] != CONDITION_NONE]
    if not conditional:
        return []
    problems = []
    if len(conditional) != len(parsed):
        problems.append((BRANCH_MIXED, None))
    for edge, (status, _variable, _equals) in conditional:
        if status != CONDITION_VALID:
            problems.append((BRANCH_INVALID_CONDITION, edge.get('id')))
    variables = sorted({variable for _edge, (status, variable, _equals) in conditional if status == CONDITION_VALID})
    if len(variables) > 1:
        problems.append((BRANCH_MULTIPLE_VARIABLES, variables))
    seen, reported = set(), set()
    for _edge, (status, variable, equals) in conditional:
        if status == CONDITION_VALID:
            key = _condition_key(variable, equals)
            if key in seen and key not in reported:
                problems.append((BRANCH_DUPLICATE_CONDITION, (variable, equals)))
                reported.add(key)
            seen.add(key)
    return problems
