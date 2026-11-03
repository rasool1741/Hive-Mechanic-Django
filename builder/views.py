# pylint: disable=no-member, line-too-long, too-many-lines, unsupported-binary-operation
# -*- coding: utf-8 -*-

import datetime
import json
import math
import mimetypes
import os
import hashlib
import mimetypes
import tempfile
import urllib

from urllib.parse import urlparse

import arrow
import requests

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.http import HttpResponse, Http404, FileResponse, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views import defaults

from filer.admin.clipboardadmin import ajax_upload
from filer.models import filemodels

from activity_logger.models import LogItem, LogTag
from integrations.models import Integration
from user_creation.decorators import user_accepted_all_terms

from .models import City, Game, GameVersion, InteractionCard, Player, Session, SessionEvent, ScoreLedger, DataProcessor, SiteSettings, StateVariable, SessionMediaSubmission
from .urban_tools import (
    CONDITION_INVALID, CONDITION_NONE, accepted_answers, branch_problems, inside_radius, match_answer, parse_condition,
    text_max_length, tool_variable_name, validate_location_sample, verify_qr_token,
)


def public_city_catalog(request):
    cities = City.objects.filter(
        is_active=True,
        games__status=Game.STATUS_PUBLISHED,
        games__archived=None,
        games__versions__status=GameVersion.STATUS_PUBLISHED,
    ).distinct().order_by('name')
    payload = []
    for city in cities:
        payload.append({
            'slug': city.slug,
            'name': city.name,
            'country': city.country,
            'region': city.region,
            'description': city.description,
            'games_count': city.games.filter(
                status=Game.STATUS_PUBLISHED,
                archived=None,
                versions__status=GameVersion.STATUS_PUBLISHED,
            ).distinct().count(),
        })
    return JsonResponse({'cities': payload})


def public_city_games(request, city_slug):
    city = get_object_or_404(City, slug=city_slug, is_active=True)
    games = Game.objects.filter(
        city=city,
        status=Game.STATUS_PUBLISHED,
        archived=None,
        versions__status=GameVersion.STATUS_PUBLISHED,
    ).distinct().order_by('name')
    payload = [{
        'slug': game.slug,
        'name': game.name,
        'description': game.description,
        'difficulty': game.difficulty,
        'estimated_minutes': game.estimated_minutes,
        'is_team_game': game.is_team_game,
    } for game in games]
    return JsonResponse({'city': city.slug, 'games': payload})


def public_game_detail(request, game_slug):
    # Do not join on versions here: a game with several Published versions
    # would match several rows and raise MultipleObjectsReturned.
    game = get_object_or_404(Game.objects.select_related('city'), slug=game_slug, status=Game.STATUS_PUBLISHED, archived=None)
    if game.published_version() is None:
        raise Http404('Game has no published version.')
    if game.city is not None and not game.city.is_active:
        raise Http404('City is inactive.')
    return JsonResponse({
        'slug': game.slug,
        'name': game.name,
        'description': game.description,
        'difficulty': game.difficulty,
        'estimated_minutes': game.estimated_minutes,
        'is_team_game': game.is_team_game,
        'city': game.city.slug if game.city else None,
    })


@login_required
@user_accepted_all_terms
@transaction.atomic
def game_action(request, session_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)

    session = Session.objects.select_for_update().select_related(
        'player', 'game_version', 'game_version__game', 'game_version__game__city',
    ).filter(pk=session_id, player__user=request.user).first()
    if session is None:
        return JsonResponse({'error': 'Session not found.'}, status=404)
    if session.completed is not None:
        return JsonResponse({'error': 'Session is already completed.'}, status=409)
    if session.game_version.status != GameVersion.STATUS_PUBLISHED:
        return JsonResponse({'error': 'Game version is not published.'}, status=409)
    if session.game_version.game.city is not None and not session.game_version.game.city.is_active:
        return JsonResponse({'error': 'City is inactive.'}, status=409)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (TypeError, ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON.'}, status=400)

    event_id = payload.get('event_id')
    stage_id = payload.get('stage_id')
    if not event_id or not stage_id:
        return JsonResponse({'error': 'event_id and stage_id are required.'}, status=400)

    previous = SessionEvent.objects.filter(session=session, event_id=event_id).first()
    if previous is not None:
        return JsonResponse(previous.result, status=200)

    definition = json.loads(session.game_version.definition or '{}')
    graph = definition.get('graph') if isinstance(definition, dict) else None
    if isinstance(graph, dict) and graph.get('nodes'):
        return _graph_game_action(session, payload, event_id, stage_id, graph)

    sequences = definition if isinstance(definition, list) else definition.get('sequences', [])
    stages = [
        '%s#%s' % (sequence.get('id'), item.get('id'))
        for sequence in sequences
        for item in sequence.get('items', [])
        if sequence.get('id') and item.get('id')
    ]
    current_index = session.session_state.get('stage_index', 0)
    if current_index >= len(stages) or stage_id != stages[current_index]:
        return JsonResponse({'error': 'Stage is not the current server stage.'}, status=409)

    gps = payload.get('gps')
    city = session.game_version.game.city
    if gps is not None:
        errors = validate_location_sample(gps.get('latitude'), gps.get('longitude'), gps.get('accuracy', 0))
        if errors or city is None or city.center_latitude is None or not inside_radius(
                gps['latitude'], gps['longitude'], city.center_latitude, city.center_longitude, 100):
            return JsonResponse({'error': 'GPS validation failed.'}, status=403)

    qr_token = payload.get('qr_token')
    if qr_token is not None:
        qr = verify_qr_token(qr_token, getattr(settings, 'GAME_ACTION_QR_SECRET', settings.SECRET_KEY))
        if qr is None or qr.get('game') != session.game_version.game.slug or qr.get('stage') != stage_id:
            return JsonResponse({'error': 'QR validation failed.'}, status=403)

    next_index = current_index + 1
    completed = next_index >= len(stages)
    result = {
        'accepted': True,
        'score': 100,
        'stage_id': stage_id,
        'next_stage_id': None if completed else stages[next_index],
        'completed': completed,
    }
    event = SessionEvent.objects.create(
        session=session, event_id=event_id, event_type='complete-stage',
        payload={'stage_id': stage_id}, result=result, accepted=True,
    )
    ScoreLedger.objects.create(session=session, event=event, reason='stage-complete', amount=100)
    session.session_state = dict(session.session_state, stage_index=next_index)
    if completed:
        session.completed = timezone.now()
    session.save(update_fields=['session_state', 'completed'])
    return JsonResponse(result)


@login_required
@user_accepted_all_terms
@transaction.atomic
def player_media_upload(request, session_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)
    session = Session.objects.select_for_update().select_related('player', 'game_version').filter(
        pk=session_id, player__user=request.user, completed__isnull=True,
    ).first()
    if session is None:
        return JsonResponse({'error': 'Session not found or inactive.'}, status=404)
    if session.game_version.status != GameVersion.STATUS_PUBLISHED:
        return JsonResponse({'error': 'Game version is not published.'}, status=409)
    try:
        definition = json.loads(session.game_version.definition or '{}')
        graph = definition.get('graph', {})
        state = dict(session.session_state or {})
        node_id = state.get('graph_node_id', graph.get('start'))
        node = next(node for node in graph.get('nodes', []) if node.get('id') == node_id)
    except (TypeError, ValueError, StopIteration):
        return JsonResponse({'error': 'Graph node is invalid.'}, status=409)
    tools = [tool for tool in node.get('tools', []) if isinstance(tool, dict)]
    tool = next((item for item in tools if item.get('type') in ('camera', 'image', 'audio', 'video', 'audio_video') and
                 (item.get('settings') or {}).get('allow_upload')), None)
    if tool is None:
        return JsonResponse({'error': 'Player media upload is not enabled for this node.'}, status=403)
    uploaded = request.FILES.get('file')
    event_id = request.POST.get('event_id')
    if uploaded is None or not event_id:
        return JsonResponse({'error': 'file and event_id are required.'}, status=400)
    if SessionMediaSubmission.objects.filter(session=session, event_id=event_id).exists():
        return JsonResponse({'error': 'event_id already used.'}, status=409)
    settings_data = tool.get('settings') or {}
    media_type = settings_data.get('media_type', 'image')
    limits = {'image': (getattr(settings, 'PLAYER_IMAGE_MAX_BYTES', 10 * 1024 * 1024), ('image/jpeg', 'image/png', 'image/webp')),
              'audio': (getattr(settings, 'PLAYER_AUDIO_MAX_BYTES', 25 * 1024 * 1024), ('audio/mpeg', 'audio/wav', 'audio/ogg')),
              'video': (getattr(settings, 'PLAYER_VIDEO_MAX_BYTES', 50 * 1024 * 1024), ('video/mp4', 'video/webm'))}
    if media_type not in limits:
        return JsonResponse({'error': 'Unsupported upload type.'}, status=403)
    maximum, allowed_mimes = limits[media_type]
    total = 0
    digest = hashlib.sha256()
    oversized = False
    with tempfile.NamedTemporaryFile(delete=False, suffix='.upload') as temporary:
        temporary_path = temporary.name
        for chunk in uploaded.chunks():
            total += len(chunk)
            if total > maximum:
                oversized = True
                break
            digest.update(chunk)
            temporary.write(chunk)
    if oversized:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
        return JsonResponse({'error': 'File is too large.'}, status=413)
    detected = None
    try:
        with open(temporary_path, 'rb') as stream:
            header = stream.read(16)
        if media_type == 'image':
            from PIL import Image
            with Image.open(temporary_path) as image:
                image.verify()
                detected = Image.MIME.get(image.format)
        elif media_type == 'audio' and header.startswith((b'ID3', b'\xff\xfb', b'OggS', b'RIFF')):
            detected = 'audio/mpeg' if header.startswith((b'ID3', b'\xff\xfb')) else 'audio/ogg'
        elif media_type == 'video' and (header[4:8] == b'ftyp' or header.startswith(b'\x1a\x45\xdf\xa3')):
            detected = 'video/mp4' if header[4:8] == b'ftyp' else 'video/webm'
    except (OSError, ValueError):
        detected = None
    if detected not in allowed_mimes:
        os.unlink(temporary_path)
        return JsonResponse({'error': 'File content is invalid.'}, status=415)
    try:
        with open(temporary_path, 'rb') as stream:
            submission = SessionMediaSubmission(
                session=session, player=session.player, node_id=node_id, event_id=event_id,
                media_type=media_type, detected_mime=detected, size=total, sha256=digest.hexdigest(),
            )
            submission.file.save('submission-%s' % digest.hexdigest(), stream, save=False)
            submission.save()
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return JsonResponse({'accepted': True, 'submission_id': submission.pk, 'node_id': node_id,
                         'media_type': media_type, 'size': total, 'detected_mime': detected})


@login_required
@user_accepted_all_terms
def player_media_download(request, submission_id):
    submission = SessionMediaSubmission.objects.select_related('session', 'player').filter(
        pk=submission_id, session__player__user=request.user,
        status=SessionMediaSubmission.STATUS_VALID,
    ).first()
    if submission is None or not submission.file or not submission.file.storage.exists(submission.file.name):
        raise Http404('Media submission not found.')
    response = FileResponse(submission.file.open('rb'), content_type=submission.detected_mime)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    response['Content-Disposition'] = 'attachment; filename="submission-%s"' % submission.pk
    return response


# The only operations a graph node accepts. Anything else is rejected before any tool runs,
# so an unknown operation can never skip the Quiz/Text/Timer checks that guard a transition.
GRAPH_OPERATIONS = ('complete', 'hint', 'media')


def _graph_condition_matches(condition, graph_vars):
    """Evaluate an edge condition under the one contract shared with publish validation.

    No condition (None) always matches. Only graph_vars is read: the variables the server
    wrote after validating a Quiz or Text answer. Nothing else in the session or the request
    can satisfy a condition, and an invalid condition (including {}, [], '' or 0) never
    matches, it is not treated as "no condition".
    """
    status, variable, equals = parse_condition(condition)
    if status == CONDITION_NONE:
        return True
    if status == CONDITION_INVALID:
        return False
    return variable in graph_vars and graph_vars[variable] == equals


def _graph_game_action(session, payload, event_id, stage_id, graph):
    nodes = {node.get('id'): node for node in graph.get('nodes', []) if isinstance(node, dict) and node.get('id')}
    edges = [edge for edge in graph.get('edges', []) if isinstance(edge, dict)]
    start = graph.get('start')
    end = graph.get('end')
    if start not in nodes or end not in nodes:
        return JsonResponse({'error': 'Published graph is invalid.'}, status=409)

    state = dict(session.session_state or {})
    current = state.get('graph_node_id', start)
    if current not in nodes or stage_id != current:
        return JsonResponse({'error': 'Stage is not the current graph node.'}, status=409)

    node = nodes[current]
    tools = [tool for tool in node.get('tools', []) if isinstance(tool, dict) and tool.get('type')]
    tool_types = {tool.get('type') for tool in tools}
    supported_tools = {'gps', 'qr', 'hint', 'timer', 'quiz', 'text', 'text_input', 'image', 'audio', 'video', 'camera', 'audio_video'}
    if tool_types - supported_tools:
        return JsonResponse({'error': 'Node contains an unsupported tool.'}, status=409)
    operation = payload.get('operation', 'complete')
    if not isinstance(operation, str) or operation not in GRAPH_OPERATIONS:
        return JsonResponse({'error': 'Unsupported operation.'}, status=400)
    media_tool = next((tool for tool in tools if tool.get('type') in ('image', 'audio', 'video', 'camera', 'audio_video')), None)
    if operation == 'media':
        if media_tool is None:
            return JsonResponse({'error': 'Media is not available for this node.'}, status=403)
        media_settings = media_tool.get('settings') or {}
        try:
            file_id = int(media_settings.get('file_id'))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'Media configuration is incomplete.'}, status=409)
        media_file = filemodels.File.objects.filter(pk=file_id).first()
        if media_file is None or not media_file.file or not media_file.has_all_mandatory_data:
            return JsonResponse({'error': 'Media file is unavailable.'}, status=404)
        media_kind = media_settings.get('media_type', media_tool.get('type'))
        allowed_prefix = {'image': 'image/', 'audio': 'audio/', 'video': 'video/'}.get(media_kind)
        if allowed_prefix is None or not (media_file.mime_type or '').lower().startswith(allowed_prefix):
            return JsonResponse({'error': 'Media type is not allowed.'}, status=403)
        return JsonResponse({'accepted': True, 'tool': media_kind, 'stage_id': current,
                             'file_id': media_file.pk, 'filename': media_file.original_filename,
                             'url': media_file.file.url})
    upload_tools = [tool for tool in tools if (tool.get('settings') or {}).get('allow_upload')]
    required_upload = next((tool for tool in upload_tools if (tool.get('settings') or {}).get('required')), None)
    submission_id = payload.get('submission_id')
    if required_upload is not None:
        submission = SessionMediaSubmission.objects.filter(
            pk=submission_id, session=session, player=session.player, node_id=current,
            status=SessionMediaSubmission.STATUS_VALID,
        ).first()
        if submission is None:
            return JsonResponse({'error': 'A valid media submission is required.'}, status=403)
    if operation == 'hint':
        hint_tool = next((tool for tool in tools if tool.get('type') == 'hint'), None)
        if hint_tool is None:
            return JsonResponse({'error': 'Hint is not available for this node.'}, status=403)
        settings_data = hint_tool.get('settings') or {}
        hint = session.game_version.hints.filter(stage_id=current, enabled=True).order_by('position').first()
        content = hint.content if hint is not None else settings_data.get('content')
        if not content:
            return JsonResponse({'error': 'Hint configuration is incomplete.'}, status=409)
        cost = hint.score_cost if hint is not None else int(settings_data.get('score_cost', 0) or 0)
        result = {'accepted': True, 'tool': 'hint', 'stage_id': current, 'content': content, 'cost': cost}
        event = SessionEvent.objects.create(session=session, event_id=event_id, event_type='use-hint',
                                            payload={'stage_id': current}, result=result, accepted=True)
        if cost:
            ScoreLedger.objects.create(session=session, event=event, reason='hint-cost', amount=-cost)
        return JsonResponse(result)

    # Only 'complete' gets here (media and hint returned above). Before anything is written (the
    # timer start, a validated answer, the move), the current node's outgoing edges must follow the
    # same branching rules that publishing enforces. A graph published earlier, or edited by hand,
    # could otherwise still have the runtime choose a path those rules forbid.
    if branch_problems([edge for edge in edges if edge.get('source') == current]):
        return JsonResponse({'error': 'Graph node has an invalid branching structure.'}, status=409)
    # A Text tool whose max_length is invalid (an older published definition) fails closed with a
    # controlled error, before the timer starts or an answer is kept; never a 500 and never a guess.
    if any(tool.get('type') in ('text', 'text_input') and text_max_length(tool.get('settings')) is None for tool in tools):
        return JsonResponse({'error': 'Text input configuration is invalid.'}, status=409)

    timer_tools = [tool for tool in tools if tool.get('type') == 'timer']
    if timer_tools:
        timer_settings = timer_tools[0].get('settings') or {}
        timer_key = 'graph_timer_started_at:%s' % current
        started_at = state.get(timer_key)
        if started_at is None:
            state[timer_key] = timezone.now().timestamp()
            session.session_state = state
            session.save(update_fields=['session_state'])
            started_at = state[timer_key]
        minimum = float(timer_settings.get('minimum_seconds', timer_settings.get('duration_seconds', 0)) or 0)
        if operation == 'complete' and timezone.now().timestamp() - float(started_at) < minimum:
            return JsonResponse({'error': 'Timer has not elapsed.'}, status=409)

    answered = {}  # variables set by this action; saved only together with the node transition
    for tool in tools:
        settings_data = tool.get('settings') or {}
        if tool.get('type') == 'quiz' and operation == 'complete':
            accepted = accepted_answers(settings_data, 'quiz')
            if 'answer' not in payload or accepted is None:
                return JsonResponse({'error': 'Quiz answer is required.'}, status=400)
            matched = match_answer('quiz', payload['answer'], accepted)
            if matched is None:
                return JsonResponse({'error': 'Quiz answer is incorrect.'}, status=403)
            answered[tool_variable_name(current, tool, 'quiz')] = matched
        if tool.get('type') in ('text', 'text_input') and operation == 'complete':
            value = payload.get('text')
            if not isinstance(value, str) or not value.strip():
                return JsonResponse({'error': 'Text input is required.'}, status=400)
            maximum = text_max_length(settings_data)
            if len(value) > maximum:
                return JsonResponse({'error': 'Text input is too long.'}, status=400)
            accepted = accepted_answers(settings_data, 'text')
            if accepted is not None:
                matched = match_answer('text', value, accepted)
                if matched is None:
                    return JsonResponse({'error': 'Text input is incorrect.'}, status=403)
                answered[tool_variable_name(current, tool, 'text')] = matched
    city = session.game_version.game.city
    gps = payload.get('gps')
    if 'gps' in tool_types:
        if not isinstance(gps, dict):
            return JsonResponse({'error': 'GPS input is required.'}, status=403)
    if gps is not None:
        errors = validate_location_sample(gps.get('latitude'), gps.get('longitude'), gps.get('accuracy', 0))
        if errors or city is None or city.center_latitude is None or not inside_radius(
                gps['latitude'], gps['longitude'], city.center_latitude, city.center_longitude, 100):
            return JsonResponse({'error': 'GPS validation failed.'}, status=403)

    qr_token = payload.get('qr_token')
    if 'qr' in tool_types and not qr_token:
        return JsonResponse({'error': 'QR input is required.'}, status=403)
    if qr_token is not None:
        qr = verify_qr_token(qr_token, getattr(settings, 'GAME_ACTION_QR_SECRET', settings.SECRET_KEY))
        if qr is None or qr.get('game') != session.game_version.game.slug or qr.get('stage') != current:
            return JsonResponse({'error': 'QR validation failed.'}, status=403)

    graph_vars = dict(state['graph_vars']) if isinstance(state.get('graph_vars'), dict) else {}
    graph_vars.update(answered)
    candidates = [edge for edge in edges if edge.get('source') == current and
                  _graph_condition_matches(edge.get('condition'), graph_vars)]
    if len(candidates) > 1:
        return JsonResponse({'error': 'Graph has ambiguous outgoing conditions.'}, status=409)
    next_node = candidates[0].get('target') if candidates else None
    if next_node is not None and next_node not in nodes:
        return JsonResponse({'error': 'Graph edge targets an unknown node.'}, status=409)
    completed = current == end or next_node is None and current == end
    if next_node is None and not completed:
        return JsonResponse({'error': 'Current graph node has no valid outgoing edge.'}, status=409)

    result = {
        'accepted': True, 'score': 100, 'stage_id': current,
        'next_stage_id': None if completed else next_node, 'completed': completed,
    }
    event = SessionEvent.objects.create(
        session=session, event_id=event_id, event_type='complete-graph-node',
        payload={'stage_id': current}, result=result, accepted=True,
    )
    ScoreLedger.objects.create(session=session, event=event, reason='graph-node-complete', amount=100)
    state['graph_node_id'] = None if completed else next_node
    state['runtime'] = 'graph'
    if answered:
        state['graph_vars'] = graph_vars
    session.session_state = state
    if completed:
        session.completed = timezone.now()
    session.save(update_fields=['session_state', 'completed'])
    return JsonResponse(result)

@login_required
@user_accepted_all_terms
def builder_home(request): # pylint: disable=unused-argument
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('View permission required.')

    context = {}

    return render(request, 'builder_home.html', context=context)

@login_required
@user_accepted_all_terms
def builder_activities(request): # pylint: disable=unused-argument
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('View permission required.')

    context = {
        'activities': [],
        'templates': [],
    }

    search = request.GET.get('q', None)

    context['query'] = search

    games = Game.objects.filter(archived=None).order_by(Lower('name'))

    if search is not None:
        query = Q(name__icontains=search) | Q(slug__icontains=search) # pylint: disable=unsupported-binary-operation

        games = Game.objects.filter(query).order_by(Lower('name'))

    for game in games:
        if game.can_edit(request.user):
            context['activities'].append(game)
        elif game.can_view(request.user):
            context['activities'].append(game)

        if game.is_template:
            context['templates'].append(game)

    return render(request, 'builder_activities.html', context=context)

@login_required
@user_accepted_all_terms
def builder_activity_logger_legacy(request): # pylint: disable=unused-argument, too-many-branches, too-many-statements
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('View permission required.')

    context = {}

    query = Q(pk__gte=0)

    tag = request.GET.get('tag', '')

    if tag != '':
        query = query & Q(tags__tag=tag)

        context['selected_tag'] = tag

    start = timezone.now() - datetime.timedelta(days=30)

    query = query & Q(logged__gte=start)

    player = request.GET.get('player', None)

    if player is not None:
        query = query & Q(player__pk=int(player))

    session = request.GET.get('session', None)

    if session is not None:
        query = query & Q(session__pk=int(session))

    game = request.GET.get('game', None)

    if game is not None:
        query = query & Q(game_version__game__pk=int(game))

    sort = request.GET.get('sort', '-logged')

    items_per_page = int(request.GET.get('size', '25'))
    page_index = int(request.GET.get('page', '0'))

    start = page_index * items_per_page
    end = start + items_per_page

    context['total_count'] = LogItem.objects.filter(query).order_by(sort).count()
    context['log_items'] = LogItem.objects.filter(query).order_by(sort)[start:end]

    context['page_index'] = page_index
    context['page_count'] = int(context['total_count'] / items_per_page)

    if context['total_count'] > items_per_page and page_index < (context['page_count'] - 1):
        context['next_page'] = '%s?page=%d' % (reverse('builder_activity_logger'), page_index + 1)

        if tag != '':
            context['next_page'] += '&tag=%s' % tag

        if player is not None:
            context['next_page'] += '&player=%s' % player

        if session is not None:
            context['next_page'] += '&session=%s' % session

        if game is not None:
            context['next_page'] += '&game=%s' % game

        if sort is not None:
            context['next_page'] += '&sort=%s' % sort

        if items_per_page is not None:
            context['next_page'] += '&size=%s' % items_per_page

    if context['total_count'] > items_per_page and page_index > 0:
        context['prior_page'] = '%s?page=%d' % (reverse('builder_activity_logger'), page_index - 1)

        if tag != '':
            context['prior_page'] += '&tag=%s' % tag

        if player is not None:
            context['prior_page'] += '&player=%s' % player

        if session is not None:
            context['prior_page'] += '&session=%s' % session

        if game is not None:
            context['prior_page'] += '&game=%s' % game

        if sort is not None:
            context['prior_page'] += '&sort=%s' % sort

        if items_per_page is not None:
            context['prior_page'] += '&size=%s' % items_per_page

    return render(request, 'builder_activity_logger.html', context=context)

@login_required
@user_accepted_all_terms
def builder_sessions(request): # pylint: disable=unused-argument
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('View permission required.')

    context = {}

    context['sessions'] = Session.objects.all().order_by('-started')

    return render(request, 'builder_sessions.html', context=context)

@login_required
@user_accepted_all_terms
def builder_authors(request): # pylint: disable=unused-argument, too-many-branches, too-many-statements
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('View permission required.')

    if request.method == 'POST':
        response_payload = {
            'message': 'No matching author located.'
        }

        action = request.POST.get('action', None)
        email = request.POST.get('email', None)

        user = get_user_model().objects.filter(email=email).first()

        if user is not None:
            if action == 'delete_user':
                user.delete()

                response_payload['message'] = '%s removed from the system.' % email
            elif action == 'activate_user':
                user.is_active = True
                user.save()

                response_payload['message'] = '%s activated.' % email
            elif action == 'deactivate_user':
                user.is_active = False
                user.save()

                response_payload['message'] = '%s deactivated.' % email
            elif action == 'promote_user':
                current_group = user.groups.all().first()

                if current_group is None:
                    reader_group = Group.objects.filter(name='Hive Mechanic Reader').first()
                    user.groups.clear()
                    user.groups.add(reader_group)
                    user.save()
                elif current_group.name == 'Hive Mechanic Reader':
                    editor_group = Group.objects.filter(name='Hive Mechanic Game Editor').first()
                    user.groups.clear()
                    user.groups.add(editor_group)
                    user.save()
                elif current_group.name == 'Hive Mechanic Game Editor':
                    manager_group = Group.objects.filter(name='Hive Mechanic Manager').first()
                    user.groups.clear()
                    user.groups.add(manager_group)
                    user.save()

                response_payload['message'] = '%s promoted.' % email
            elif action == 'demote_user':
                current_group = user.groups.all().first()

                if current_group is None:
                    pass
                elif current_group.name == 'Hive Mechanic Manager':
                    editor_group = Group.objects.filter(name='Hive Mechanic Game Editor').first()
                    user.groups.clear()
                    user.groups.add(editor_group)
                    user.save()
                elif current_group.name == 'Hive Mechanic Game Editor':
                    reader_group = Group.objects.filter(name='Hive Mechanic Reader').first()
                    user.groups.clear()
                    user.groups.add(reader_group)
                    user.save()
                else:
                    user.groups.clear()
                    user.save()

                response_payload['message'] = '%s demoted.' % email
            else:
                response_payload['message'] = 'Unknown action: %s' % action

        return HttpResponse(json.dumps(response_payload, indent=2), content_type='application/json', status=200)

    context = {}

    context['pending'] = list(get_user_model().objects.filter(is_active=False))
    context['active'] = get_user_model().objects.filter(is_active=True).order_by('email')

    context['is_manager'] = (request.user.groups.all().filter(name='Hive Mechanic Manager').first() is not None)

    if context['is_manager'] is False:
        context['pending'] = []

    return render(request, 'builder_authors.html', context=context)

@login_required
@user_accepted_all_terms
def builder_sessions_action(request):
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('View permission required.')

    if request.method == 'POST':
        session = Session.objects.filter(pk=int(request.POST.get('session', -1))).first()
        action = request.POST.get('action', None)

        if session is not None:
            matched_game = session.game_version.game

            if matched_game.can_edit(request.user):
                if action == 'delete':
                    session.complete()
                    session.delete()
                elif action == 'cancel':
                    session.complete()

                return HttpResponse(json.dumps({'success': True}, indent=2), content_type='application/json', status=200)

            raise PermissionDenied('Edit permission required.')

    raise PermissionDenied('View permission required.')

@login_required
@user_accepted_all_terms
def builder_players(request): # pylint: disable=unused-argument
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('View permission required.')

    context = {}

    context['players'] = Player.objects.all()

    return render(request, 'builder_players.html', context=context)


@login_required
@user_accepted_all_terms
def builder_game(request, game): # pylint: disable=unused-argument
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('View permission required.')

    matched_game = get_object_or_404(Game, slug=game)

    if matched_game.can_view(request.user): # pylint: disable=too-many-nested-blocks
        context = {}

        context['game'] = matched_game
        context['editable'] = matched_game.can_edit(request.user)

        if request.method == 'POST':
            if context['editable']:
                definition = json.loads(request.POST['definition'])

                new_version = GameVersion(game=context['game'], created=timezone.now(), definition=json.dumps(definition, indent=2), creator=request.user)
                new_version.save()

                game_name = definition.get('name', None)
                game_identifier = slugify(game_name)

                slug_index = 1

                if game_identifier != game:
                    while Game.objects.filter(slug=game_identifier).count() > 0:
                        game_identifier = '%s-%d' % (slugify(game_name), slug_index)

                        slug_index += 1

                payload = {
                    'success': True
                }

                if (game_name is not None and game_identifier is not None):
                    updated = False

                    if context['game'].name != game_name:
                        context['game'].name = game_name

                        updated = True

                    if context['game'].slug != game_identifier:
                        base_identifier = game_identifier
                        base_index = 1

                        while Game.objects.filter(slug=game_identifier).count() > 0:
                            game_identifier = '%s-%s' % (base_identifier, base_index)

                            base_index += 1

                        context['game'].slug = game_identifier

                        definition['identifier'] = game_identifier
                        new_version.definition = json.dumps(definition, indent=2)
                        new_version.save()

                        updated = True

                        payload['redirect_url'] = reverse('builder_game', args=[context['game'].slug])

                    if updated:

                        context['game'].save()

                return HttpResponse(json.dumps(payload, indent=2), content_type='application/json', status=200)

            raise PermissionDenied('Edit permission required.')

        return render(request, 'builder_js.html', context=context)

    raise PermissionDenied('View permission required.')


@login_required
@user_accepted_all_terms
def builder_graph_api(request, game):
    """Read and persist the optional graph metadata for a draft definition."""
    if not request.user.has_perm('builder.builder_login'):
        raise PermissionDenied('View permission required.')

    matched_game = get_object_or_404(Game, slug=game)
    if not matched_game.can_view(request.user):
        raise PermissionDenied('View permission required.')

    latest = matched_game.versions.order_by('-created').first()
    if latest is None:
        return JsonResponse({'error': 'Game has no definition.'}, status=404)

    try:
        definition = json.loads(latest.definition or '{}')
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Game definition is not valid JSON.'}, status=409)
    if isinstance(definition, list):
        definition = {'sequences': definition}
    if not isinstance(definition, dict):
        return JsonResponse({'error': 'Game definition must be an object.'}, status=409)

    if request.method == 'GET':
        return JsonResponse({
            'graph': definition.get('graph', {'nodes': [], 'edges': [], 'start': None, 'end': None}),
            'editable': matched_game.can_edit(request.user),
            'runtime': 'sequence-interaction-card',
        })

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)
    if not matched_game.can_edit(request.user):
        raise PermissionDenied('Edit permission required.')

    try:
        payload = json.loads(request.body.decode('utf-8'))
        graph = payload.get('graph', payload) if isinstance(payload, dict) else None
    except (TypeError, ValueError, UnicodeDecodeError):
        graph = None
    if not isinstance(graph, dict):
        return JsonResponse({'error': 'Graph must be a JSON object.'}, status=400)

    candidate = dict(definition)
    candidate['graph'] = graph
    draft = GameVersion(
        game=matched_game,
        created=timezone.now(),
        definition=json.dumps(candidate, indent=2),
        creator=request.user,
        status=GameVersion.STATUS_DRAFT,
    )
    draft.validate_definition()
    with transaction.atomic():
        draft.save()
    return JsonResponse({
        'success': True,
        'version_id': draft.pk,
        'graph': graph,
        'validation_errors': draft.validation_errors,
        'runtime': 'Graph is currently editor and publication metadata; runtime follows sequences.',
    })


@login_required
@user_accepted_all_terms
def builder_game_definition_json(request, game): # pylint: disable=unused-argument
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('View permission required.')

    matched_game = Game.objects.filter(slug=game).first()

    if matched_game.can_view(request.user):
        latest = matched_game.versions.order_by('-created').first()

        if latest is None:
            latest = GameVersion(game=matched_game, created=timezone.now())

            definition = {
                'sequences': [{
                    'type': 'sequence',
                    'id': 'new-sequence',
                    'name': 'New Sequence',
                    'items': [{
                        "name": "Hello World",
                        "context": "Start building your game here.",
                        "message": "Hello World",
                        "type": "send-message",
                        "id": "hello-world"
                    }]
                }],
                'interrupts': [],
                'name': matched_game.name,
                'identifier': matched_game.slug,
                'initial-card': 'new-sequence#hello-world',
                'variables': [],
                'incoming_call_interrupt': ''
            }

            latest.definition = json.dumps(definition, indent=2)
            latest.save()

        definition = None

        try:
            definition = json.loads(latest.definition)

            if isinstance(definition, list):
                definition = {
                    'sequences': definition,
                    'interrupts': [],
                    'name': matched_game.name,
                    'identifier': matched_game.slug,
                    'initial-card': '',
                    'variables': [],
                    'incoming_call_interrupt': ''
                }

            if definition.get('name', '') == '':
                definition['name'] = matched_game.name

            if definition.get('identifier', '') == '':
                definition['identifier'] = matched_game.slug

            if definition.get('initial-card', '') == '':
                definition['initial-card'] = definition['sequences'][0]['id'] + '#' + definition['sequences'][0]['items'][0]['id']

            if definition.get('incoming_call_interrupt', '') == '':
                definition['incoming_call_interrupt'] = definition['sequences'][0]['id'] + '#' + definition['sequences'][0]['items'][0]['id']

        except json.JSONDecodeError:
            definition = [{
                'type': 'sequence',
                'id': 'new-sequence',
                'name': 'Error',
                'items': [{
                    "name": "Parsing Error",
                    "context": "Error reading game",
                    "comment": "Unable to parse game definition.\n\nPlease verify that the JSON definition is valid.",
                    "type": "comment",
                    "id": "error"
                }]
            }]

        response = HttpResponse(json.dumps(definition, indent=2), content_type='application/json', status=200)

        response['X-Hive-Mechanic-Editable'] = matched_game.can_edit(request.user)

        return response

    raise PermissionDenied('View permission required.')


@login_required
@user_accepted_all_terms
def builder_game_variables(request, game): # pylint: disable=unused-argument
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('View permission required.')

    matched_game = Game.objects.filter(slug=game).first()

    if matched_game.can_view(request.user):
        variables = []

        for variable_name in matched_game.game_state.keys():
            variables.append({
                'name': variable_name,
                'value': matched_game.game_state[variable_name]
            })

        response = HttpResponse(json.dumps(variables, indent=2), content_type='application/json', status=200)

        response['X-Hive-Mechanic-Editable'] = matched_game.can_edit(request.user)

        return response

    raise PermissionDenied('View permission required.')


@login_required
@user_accepted_all_terms
def builder_game_templates(request): # pylint: disable=unused-argument
    context = {}
    games = Game.objects.filter(is_template=True).order_by('name').values("name", 'slug', 'id')
    context['games'] = list(games)
    return HttpResponse(json.dumps(context, indent=2), content_type='application/json', status=200)


@login_required
@user_accepted_all_terms
def builder_interaction_card(request, card): # pylint: disable=unused-argument
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('View permission required.')

    card = get_object_or_404(InteractionCard, identifier=card)

    if card.client_implementation is not None:
        content_type = 'application/octet-stream'

        if card.client_implementation.path.endswith('.js'):
            content_type = 'application/javascript'

        response = FileResponse(open(card.client_implementation.path, 'rb'), content_type=content_type) # pylint: disable=consider-using-with
        response['Content-Length'] = os.path.getsize(card.client_implementation.path)

        return response

    raise Http404('Card implementation not found. Verify that a client implementation file is attached to the card definition.')


@login_required
@user_accepted_all_terms
def builder_add_game(request): # pylint: disable=unused-argument
    if request.user.has_perm('builder.builder_login') is False or request.user.has_perm('builder.add_game') is False:
        raise PermissionDenied('View permission required.')

    response = {
        'message': 'Unable to add game.',
        'success': False
    }

    if request.method == 'POST' and 'name' in request.POST:
        name = request.POST['name'].strip()
        template = request.POST['template'].strip()

        if name:
            slug = slugify(name)

            index = 1

            while Game.objects.filter(slug=slug).count() > 0:
                slug = slugify(name) + '-' + str(index)

                index += 1

            new_game = Game(name=name, slug=slug)
            new_game.save()

            if template and template != 'none':
                old_game = Game.objects.filter(id=template).first()
                for card in old_game.cards.all():
                    new_game.cards.add(card)

                # latest version
                version = old_game.latest_version()
                version.pk = None
                version.game = new_game

                definition = json.loads(version.definition)

                definition['identifier'] = slug
                definition['name'] = name

                version.definition = json.dumps(definition, indent=2)
                version.creator = request.user

                version.save()
                new_game.save()
            # default
            else:
                for card in InteractionCard.objects.filter(enabled=True):
                    new_game.cards.add(card)

                new_game.save()
            response['success'] = True
            response['message'] = 'Game added.'
            response['redirect'] = reverse('builder_game', args=[new_game.slug])

    return HttpResponse(json.dumps(response, indent=2), content_type='application/json', status=200)


@login_required
@user_accepted_all_terms
def builder_data_processor_options(request):  # pylint: disable=unused-argument
    options = []

    for processor in DataProcessor.objects.filter(enabled=True).order_by('name'):
        options.append({
            'value': processor.identifier,
            'label': {
                'en': processor.name
            }
        })

    return HttpResponse(json.dumps(options, indent=2), content_type='application/json', status=200)


@login_required
@user_accepted_all_terms
def builder_activity_options(request):  # pylint: disable=unused-argument
    options = []

    for activity in Game.objects.filter(is_template=False).order_by('name'):
        options.append({
            'value': activity.slug,
            'label': {
                'en': activity.name
            }
        })

    return HttpResponse(json.dumps(options, indent=2), content_type='application/json', status=200)


@login_required
@user_accepted_all_terms
def builder_activity_archive(request, slug): # pylint: disable=unused-argument
    if request.user.has_perm('builder.delete_game') is False:
        raise PermissionDenied('Delete game permission required.')

    activity = get_object_or_404(Game, slug=slug)

    if activity.can_edit(request.user):
        activity.archive()

        return redirect('builder_activities')

    raise PermissionDenied('Delete game permission required.')


@login_required
@user_accepted_all_terms
def builder_update_icon(request):
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('View permission required.')

    if request.method == 'POST':
        activity = Game.objects.filter(pk=int(request.POST.get('activity_pk'))).first()

        if activity is not None: #  and activity.can_view(request.user):
            activity.icon = request.FILES["icon_file"]

            activity.save()

            icon_details = {
                'url': activity.icon.url
            }
            response = HttpResponse(json.dumps(icon_details, indent=2), content_type='application/json', status=200)

            response['X-Hive-Mechanic-Editable'] = activity.can_edit(request.user)

            return response

    raise PermissionDenied('View permission required.')


@login_required
@user_accepted_all_terms
def builder_media(request):
    context = {}
    page = request.GET.get('page', 1)

    media_files = filemodels.File.objects.order_by('-uploaded_at')

    search = request.GET.get('q', None)

    context['query'] = search

    if search is not None:
        query = Q(original_filename__icontains=search) | Q(description__icontains=search) # pylint: disable=unsupported-binary-operation

        media_files = filemodels.File.objects.filter(query).order_by('-uploaded_at')

    paginator = Paginator(media_files, 30)

    try:
        pages = paginator.page(page)
    except PageNotAnInteger:
        pages = paginator.page(1)
    except EmptyPage:
        pages = paginator.page(paginator.num_pages)

    context['media_files'] = media_files
    context['pages'] = pages
    context['total_media_file_count'] = filemodels.File.objects.all().count()
    context['media_file_warning_size'] = settings.TEXT_MESSAGE_WARNING_FILE_SIZE

    return render(request, 'builder_media.html', context=context)

@login_required
@user_accepted_all_terms
def builder_media_upload(request):
    if request.method == 'POST':
        response = ajax_upload(request)

        res = json.loads(response.content)

        if 'error' in res:
            return redirect(defaults.HttpResponseServerError)

        description = request.POST.get("description")

        if description:
            filer_file = filemodels.File.objects.filter(id=res["file_id"]).first()
            filer_file.description = description
            filer_file.save()

    return redirect('builder_media')

@login_required
@user_accepted_all_terms
def builder_settings(request): # pylint: disable=unused-argument
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('View permission required.')

    context = {}

    site_settings = SiteSettings.objects.all().order_by('-last_updated').first()

    now = timezone.now()

    if request.method == 'POST':
        if site_settings is None:
            site_settings = SiteSettings.objects.create(name=request.POST.get('site_name', 'Hive Mechanic'), created=now, last_updated=now)

        try:
            banner_file = request.FILES["site_banner"]

            if banner_file is not None:
                site_settings.banner = request.FILES["site_banner"]
        except KeyError:
            pass

        site_settings.name = request.POST.get('site_name', 'Hive Mechanic')
        site_settings.message_of_the_day = request.POST.get('site_motd', '')
        site_settings.new_player_message = request.POST.get('site_new_player_message', 'Incoming message from Hive Mechanic. Reply STOP to block further messages.')
        site_settings.last_updated = now
        site_settings.save()

        response_payload = {}

        if bool(site_settings.banner):
            response_payload['url'] = site_settings.banner.url

        response = HttpResponse(json.dumps(response_payload, indent=2), content_type='application/json', status=200)

        return response

    if site_settings is not None:
        context['settings'] = site_settings

    return render(request, 'builder_settings.html', context=context)


@login_required
@user_accepted_all_terms
def builder_activity_view(request, slug): # pylint: disable=unused-argument
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('View permission required.')

    matched_activity = Game.objects.filter(slug=slug).first()

    if matched_activity.can_view(request.user):
        context = {}

        context['activity'] = matched_activity

        response = render(request, 'builder_view.html', context=context)
        response['X-Frame-Options'] = 'SAMEORIGIN'

        return response

    raise PermissionDenied('View permission required.')


@login_required
@user_accepted_all_terms
def builder_integrations(request):
    context = {
        'activities': [],
        'matches': [],
        'integrations': []
    }

    search = request.GET.get('q', None)

    context['query'] = search

    integrations = Integration.objects.order_by(Lower('name'))

    if search is not None:
        query = Q(name__icontains=search) | Q(url_slug__icontains=search) | Q(type__icontains=search) | Q(game__name__icontains=search) # pylint: disable=unsupported-binary-operation

        integrations = Integration.objects.filter(query).order_by(Lower('name'))

    for game in Game.objects.order_by(Lower('name')):
        activity = {
            'pk': game.pk,
            'name': game.name
        }

        integration_types = []

        for integration in game.integrations.all():
            if (integration.type in integration_types) is False:
                integration_types.append(integration.type)

        activity_type_classes = ''

        for integration_type in integration_types:
            if activity_type_classes != '':
                activity_type_classes += ' '

            activity_type_classes += 'includes_' + integration_type

        activity['integration_type_classes'] = activity_type_classes

        context['activities'].append(activity)

    for integration in integrations:
        integration_dict = {
            'pk': integration.pk,
            'name': integration.name,
            'url_slug': integration.url_slug,
            'type': integration.type,
            'activity': None,
            'enabled': integration.enabled
        }

        if integration.game is not None:
            integration_dict['activity'] = integration.game.name
            integration_dict['activity_id'] = integration.game.pk
            integration_dict['activity_slug'] = integration.game.slug

        context['integrations'].append(integration_dict)

    return render(request, 'builder_integration.html', context=context)


@login_required
@user_accepted_all_terms
def builder_integrations_update(request):
    if request.method == 'POST':
        response_payload = {}

        integration = Integration.objects.filter(pk=int(request.POST.get('id', '-1'))).first()

        if integration is not None:
            integration.name = request.POST.get('name', 'New Integration')
            integration.game = Game.objects.filter(pk=int(request.POST.get('activity', '-1'))).first()

            integration.save()

            response_payload['result'] = 'success'
            response_payload['message'] = 'Integration updated.'
            response_payload['redirect'] = reverse('builder_integrations')
        else:
            response_payload['result'] = 'error'
            response_payload['message'] = 'Unable to located specified integration.'

        response = HttpResponse(json.dumps(response_payload, indent=2), content_type='application/json', status=200)

        response['X-Hive-Mechanic-Editable'] = True

        return response

    return redirect('builder_integrations')

@login_required
@user_accepted_all_terms
def builder_activity_actions_json(request, activity): # pylint: disable=unused-argument
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('Edit permission required.')

    matched_game = Game.objects.filter(slug=activity).first()

    if matched_game.can_edit(request.user):
        context = {}

        context['game'] = matched_game

        response_payload = {
            'result': 'error',
            'message': 'No action specified'
        }

        if request.method == 'POST':
            action = request.POST.get('action', None)

            if action is not None:
                if action == 'reset-game-variables':
                    matched_game.reset_variables()

                    response_payload['result'] = 'success'
                    response_payload['message'] = 'Game variables have been cleared.'
                elif action == 'reset-active-session-variables':
                    matched_game.reset_active_session_variables()

                    response_payload['result'] = 'success'
                    response_payload['message'] = 'Game session variables have been cleared.'
                elif action == 'close-active-sessions':
                    matched_game.close_active_sessions()

                    response_payload['result'] = 'success'
                    response_payload['message'] = 'Active sessions have been closed.'
                else:
                    response_payload['message'] = 'Undefined action: %s' % action
            else:
                response_payload['message'] = 'No action provided.'

        response = HttpResponse(json.dumps(response_payload, indent=2), content_type='application/json', status=200)

        response['X-Hive-Mechanic-Editable'] = matched_game.can_edit(request.user)

        return response

    raise PermissionDenied('Edit permission required.')

@login_required
@user_accepted_all_terms
def builder_clear_variables(request): # pylint: disable=too-many-branches
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('View permission required.')

    response_payload = {
        'success': False,
        'message': 'Unable to clear variables.'
    }

    safe_keys = ('twilio_player',)

    if request.method == 'POST': # pylint: disable=too-many-nested-blocks
        player_id = int(request.POST.get('id', '-1'))
        to_clear = request.POST.get('clear', None)

        player = Player.objects.filter(pk=player_id).first()

        if player is not None:
            clear_keys = []

            if to_clear in ('all', 'site'):
                for key in player.player_state.keys():
                    if key.startswith('__') and (key in safe_keys) is False:
                        clear_keys.append(key)

            if to_clear in ('all', 'activity'):
                for key in player.player_state.keys():
                    if key.startswith('__') is False and (key in safe_keys) is False:
                        clear_keys.append(key)

            for key in clear_keys:
                del player.player_state[key]

            player.save()

            for session in player.sessions.filter(completed=None):
                clear_keys = []

                if to_clear in ('all', 'site'):
                    for key in session.session_state.keys():
                        if key.startswith('__') and (key in safe_keys) is False:
                            clear_keys.append(key)

                if to_clear in ('all', 'activity'):
                    for key in session.session_state.keys():
                        if key.startswith('__') is False and (key in safe_keys) is False:
                            clear_keys.append(key)

                for key in clear_keys:
                    del session.session_state[key]

                session.save()

            response_payload['message'] = 'Variables cleared.'
            response_payload['success'] = True
        else:
            response_payload['message'] = 'Unable to locate participant.'

    return HttpResponse(json.dumps(response_payload, indent=2), content_type='application/json', status=200)


@login_required
@user_accepted_all_terms
def builder_activity_logger(request): # pylint: disable=unused-argument, too-many-branches, too-many-statements, too-many-locals
    if request.user.has_perm('builder.builder_login') is False:
        raise PermissionDenied('View permission required.')

    context = {}

    query = Q(pk__gte=0)

    tags = request.GET.getlist('tag')

    tag_intersections = [LogItem.objects.filter(pk__gte=0)]

    for tag in tags:
        tag_intersections.append(LogItem.objects.filter(tags__tag=tag))

    context['selected_tags'] = tags

    start_date = request.GET.get('start', '')

    if start_date != '':
        start = arrow.get(start_date).datetime

        query = query & Q(logged__gte=start)

        context['start'] = start_date

    end_date = request.GET.get('end', '')

    if end_date != '':
        end = arrow.get(end_date).datetime

        end = end + datetime.timedelta(days=1)

        query = query & Q(logged__lt=end)

        context['end'] = end_date

    search_query = request.GET.get('q', '')

    if search_query != '':
        search = (Q(source__icontains=search_query) | Q(message__icontains=search_query) | Q(metadata__icontains=search_query)) # pylint: disable=unsupported-binary-operation

        query = query & search

        context['query'] = search_query

    activity_slug = request.GET.get('activity', '')

    if activity_slug != '':
        activity_query = None

        activity = Game.objects.get(slug=activity_slug)

        for version in activity.versions.all():
            if activity_query is None:
                activity_query = Q(game_version=version)
            else:
                activity_query = activity_query | Q(game_version=version) # pylint: disable=unsupported-binary-operation

        query = query & activity_query

    sort = request.GET.get('sort', '-logged')

    items_per_page = int(request.GET.get('size', '25'))
    page_index = int(request.GET.get('page', '0'))

    start = page_index * items_per_page
    end = start + items_per_page

    context['total_count'] = LogItem.objects.filter(query).intersection(*tag_intersections).count()

    end = min(end, context['total_count'])

    context['log_items'] = LogItem.objects.filter(query).intersection(*tag_intersections).order_by(sort)[start:end]

    context['page_index'] = page_index
    context['page_count'] = math.ceil(context['total_count'] / items_per_page)

    context['start_item'] = start + 1
    context['end_item'] = end

    base_url = reverse('builder_activity_logger')

    query_string = {
        'size': items_per_page,
    }

    if sort is not None:
        query_string['sort'] = sort

        context['sort'] = sort

    if search_query != '':
        query_string['q'] = search_query

    context['tags'] = []

    for item_tag in LogTag.objects.all().order_by('name'):
        query_string['tag'] = item_tag.tag

        params = urllib.parse.parse_qs(request.META.get('QUERY_STRING', ''))

        new_params = {}

        for key, value in params.items():
            if isinstance(value, str):
                value = [value]

            new_params[key] = value

        if 'tag' in new_params:
            if (item_tag.tag in new_params['tag']) is False:
                new_params['tag'].append(item_tag.tag)
        else:
            new_params['tag'] = [item_tag.tag]

        new_url = '%s?%s' % (base_url, urllib.parse.urlencode(new_params, doseq=True))

        if item_tag.tag in new_params['tag']:
            new_params['tag'].remove(item_tag.tag)

        clear_url = '%s?%s' % (base_url, urllib.parse.urlencode(new_params, doseq=True))

        context['tags'].append({
            'name': item_tag.name,
            'tag': item_tag.tag,
            'add_url': new_url,
            'clear_url': clear_url
        })

    context['clear_activity_url'] = '%s?%s' % (base_url, urllib.parse.urlencode(query_string))

    selected_activity = request.GET.get('activity', None)

    context['selected_activity'] = selected_activity

    activities = []

    for game in Game.objects.filter(is_template=False).order_by(Lower('name')):
        activities.append({
            'name': game.name,
            'slug': game.slug,
        })

    context['activities'] = activities

    del query_string['tag']

    context['clear_tag_url'] = '%s?%s' % (base_url, urllib.parse.urlencode(query_string))

    query_string['tag'] = []

    for tag in tags:
        query_string['tag'].append(tag)

    if context['total_count'] > items_per_page and page_index < context['page_count'] - 1:
        query_string['page'] = page_index + 1

        context['next_page'] = '%s?%s' % (base_url, urllib.parse.urlencode(query_string, doseq=True))

        if page_index + 1 < context['page_count']:
            query_string['page'] = context['page_count'] - 1
            context['last_page'] = '%s?%s' % (base_url, urllib.parse.urlencode(query_string, doseq=True))

    if context['page_index'] > 0:
        query_string['page'] = page_index - 1

        context['prior_page'] = '%s?%s' % (base_url, urllib.parse.urlencode(query_string, doseq=True))

        query_string['page'] = 0

        context['first_page'] = '%s?%s' % (base_url, urllib.parse.urlencode(query_string, doseq=True))

    return render(request, 'builder_activity_log_table.html', context=context)

@login_required
@user_accepted_all_terms
def builder_moderate(request): # pylint: disable=unused-argument, too-many-branches, too-many-statements, too-many-locals
    if request.user.has_perm('builder.builder_moderate') is False and request.user.has_perm('builder.builder_moderate_activity') is False:
        raise PermissionDenied('Moderation permission required.')

    if request.method == 'POST':
        moderated = 0

        variable_ids = json.loads(request.POST.get('selected_ids', '[]'))

        action = request.POST.get('action', None)

        if action is not None:
            for variable_id in variable_ids:
                state_variable = StateVariable.objects.filter(pk=int(variable_id)).first()

                if action == 'approve':
                    state_variable.moderation_approve()
                elif action == 'reject':
                    state_variable.moderation_reject()
                elif action == 'reset':
                    state_variable.moderation_reset()

                moderated += 1

        response_payload = {}

        if moderated == 1:
            response_payload['message'] = '1 item moderated.'
        else:
            response_payload['message'] = '%s items moderated.' % moderated

        return HttpResponse(json.dumps(response_payload, indent=2), content_type='application/json', status=200)

    context = {}

    query = Q(pk__gte=0)

    search_query = request.GET.get('q', '')

    if search_query != '':
        search = (Q(key__icontains=search_query) | Q(value__icontains=search_query) | Q(metadata__icontains=search_query)) # pylint: disable=unsupported-binary-operation

        query = query & search

        context['query'] = search_query

    status_slug = request.GET.get('status', 'pending')

    if status_slug != '':
        if status_slug == 'pending':
            query = query & Q(metadata__moderation_status=None)
        elif status_slug == 'approved':
            query = query & Q(metadata__moderation_status=True)
        elif status_slug == 'rejected':
            query = query & Q(metadata__moderation_status=False)
        else:
            query = query & (Q(metadata__moderation_status=False) | Q(metadata__moderation_status=True) | Q(metadata__moderation_status=None)) # pylint: disable=unsupported-binary-operation

    sort = request.GET.get('sort', '-added')

    items_per_page = int(request.GET.get('size', '25'))
    page_index = int(request.GET.get('page', '0'))

    start = page_index * items_per_page
    end = start + items_per_page

    if request.user.has_perm('builder.builder_moderate') is False:
        activity_filter = Q(pk=-1)

        session_pks = []

        for activity in request.user.builder_game_editables.all():
            activity_filter = activity_filter | Q(activity=activity)

            for version in activity.versions.all():
                for session in version.sessions.all():
                    session_pks.append(session.pk)

        activity_filter = activity_filter | Q(session__pk__in=session_pks)

        query = query & activity_filter

    context['moderate_items'] = StateVariable.objects.filter(query).order_by(sort)[start:end]

    context['total_count'] = StateVariable.objects.filter(query).count()

    end = min(end, context['total_count'])

    context['page_index'] = page_index
    context['page_count'] = math.ceil(context['total_count'] / items_per_page)

    context['start_item'] = start + 1
    context['end_item'] = end

    base_url = reverse('builder_moderate')

    query_string = {
        'size': items_per_page,
    }

    if sort is not None:
        query_string['sort'] = sort

        context['sort'] = sort

    if search_query != '':
        query_string['q'] = search_query

    context['clear_activity_url'] = '%s?%s' % (base_url, urllib.parse.urlencode(query_string))

    selected_status = request.GET.get('status', 'pending')

    context['selected_status'] = selected_status

    if context['total_count'] > items_per_page and page_index < context['page_count'] - 1:
        query_string['page'] = page_index + 1

        context['next_page'] = '%s?%s' % (base_url, urllib.parse.urlencode(query_string, doseq=True))

        if page_index + 1 < context['page_count']:
            query_string['page'] = context['page_count'] - 1
            context['last_page'] = '%s?%s' % (base_url, urllib.parse.urlencode(query_string, doseq=True))

    if context['page_index'] > 0:
        query_string['page'] = page_index - 1

        context['prior_page'] = '%s?%s' % (base_url, urllib.parse.urlencode(query_string, doseq=True))

        query_string['page'] = 0

        context['first_page'] = '%s?%s' % (base_url, urllib.parse.urlencode(query_string, doseq=True))

    return render(request, 'builder_moderate.html', context=context)

@login_required
@user_accepted_all_terms
def builder_download(request):
    url_path = request.GET.get('path', None)

    if url_path.startswith('/'):
        url_path = '%s%s' % (settings.SITE_URL, url_path)

    if url_path is None:
        raise Http404('No path provided.')

    url_response = requests.get(url_path, timeout=300)

    if 200 >= url_response.status_code < 300:
        parsed = urlparse(url_path)

        path_tokens = parsed.path.split('/')

        filename = path_tokens[-1]

        content_type = (url_response.headers.get('content-type', 'application/octet-stream'),)

        if content_type is None:
            content_type = mimetypes.guess_type(url_path)

        extension = ''

        if content_type[0] is not None:
            extension = mimetypes.guess_extension(content_type[0])

            if filename.endswith(extension) is False:
                filename = '%s%s' % (filename, extension)

        headers = {
            'Content-Disposition': 'attachment; filename="%s"' % filename
        }

        return HttpResponse(url_response.content, content_type=url_response.headers.get('content-type', 'application/octet-stream'), status=url_response.status_code, headers=headers)

    raise Http404('Received invalid status code: %s' % url_response.status_code)
