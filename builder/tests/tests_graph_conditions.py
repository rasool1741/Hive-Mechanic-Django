import json
import time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ..models import City, Game, GameVersion, Player, ScoreLedger, Session, SessionEvent
from ..views import _graph_condition_matches
from ..urban_tools import (
    BRANCH_INVALID_CONDITION, BRANCH_MIXED, BRANCH_MULTIPLE_VARIABLES, CONDITION_INVALID, CONDITION_NONE, CONDITION_VALID,
    accepted_answers, answer_key, branch_problems, graph_dominators, match_answer, parse_condition, tool_variable_name,
)

# Deliberately unusual answers so a leak is easy to spot in any response body.
ACCEPTED = ['Crimson Tide', 'Azure Wave']
LEGACY = {'sequences': [{'id': 'legacy', 'items': [{'id': 'fallback'}]}], 'initial-card': 'legacy#fallback'}


def definition_with(graph):
    return dict(LEGACY, graph=graph)


def branching_graph(tool=None):
    """pick --(palette=Crimson Tide)--> red-path, pick --(palette=Azure Wave)--> blue-path -> finish"""
    tool = tool or {'type': 'quiz', 'settings': {'answer': list(ACCEPTED), 'variable': 'palette'}}
    return {
        'nodes': [{'id': 'pick', 'tools': [tool]}, {'id': 'red-path'}, {'id': 'blue-path'}, {'id': 'finish'}],
        'edges': [
            {'id': 'e-red', 'source': 'pick', 'target': 'red-path', 'condition': {'variable': 'palette', 'equals': 'Crimson Tide'}},
            {'id': 'e-blue', 'source': 'pick', 'target': 'blue-path', 'condition': {'variable': 'palette', 'equals': 'Azure Wave'}},
            {'id': 'e-red-end', 'source': 'red-path', 'target': 'finish'},
            {'id': 'e-blue-end', 'source': 'blue-path', 'target': 'finish'},
        ],
        'start': 'pick', 'end': 'finish',
    }


class GraphRuntimeBase(TestCase):
    counter = 0

    def start(self, graph, validate=True):
        """Publish a graph (or force it published to test the runtime on unvalidated data)."""
        type(self).counter += 1
        n = type(self).counter
        user = get_user_model().objects.create_user(username='cond-player-%d' % n, password='pw')
        city = City.objects.create(slug='cond-city-%d' % n, name='Cond %d' % n, center_latitude=35.7, center_longitude=51.4)
        game = Game.objects.create(name='Cond %d' % n, slug='cond-game-%d' % n, city=city)
        version = GameVersion.objects.create(game=game, created='2026-01-01T00:00:00Z', definition=json.dumps(definition_with(graph)))
        if validate:
            published, errors = version.publish()
            self.assertTrue(published, errors)
        else:
            GameVersion.objects.filter(pk=version.pk).update(status=GameVersion.STATUS_PUBLISHED)
        player = Player.objects.create(identifier='user:cond-player-%d' % n, user=user)
        self.session = Session.objects.create(player=player, game_version=version, started='2026-01-01T00:00:00Z')
        self.client.login(username='cond-player-%d' % n, password='pw')
        self.endpoint = reverse('game_action', args=[self.session.pk])
        return self.session

    def act(self, event_id, stage_id, **payload):
        body = dict(payload, event_id=event_id, stage_id=stage_id)
        return self.client.post(self.endpoint, data=json.dumps(body), content_type='application/json')

    def state(self):
        self.session.refresh_from_db()
        return self.session.session_state


class ConditionalEdgeRuntimeTests(GraphRuntimeBase):
    def test_correct_answer_moves_to_the_matching_path_and_stores_the_authored_value(self):
        self.start(branching_graph())
        response = self.act('e1', 'pick', answer='Crimson Tide')
        self.assertEqual(200, response.status_code, response.content)
        self.assertEqual('red-path', response.json()['next_stage_id'])
        state = self.state()
        self.assertEqual('red-path', state['graph_node_id'])
        self.assertEqual({'palette': 'Crimson Tide'}, state['graph_vars'])
        self.assertEqual(1, ScoreLedger.objects.filter(session=self.session).count())

    def test_other_accepted_answer_takes_the_other_path(self):
        self.start(branching_graph())
        response = self.act('e1', 'pick', answer='Azure Wave')
        self.assertEqual('blue-path', response.json()['next_stage_id'])
        self.assertEqual('blue-path', self.state()['graph_node_id'])

    def test_stored_value_is_the_authored_one_not_what_the_player_typed(self):
        self.start(branching_graph())
        response = self.act('e1', 'pick', answer='  azure WAVE ')
        self.assertEqual('blue-path', response.json()['next_stage_id'])
        self.assertEqual({'palette': 'Azure Wave'}, self.state()['graph_vars'])

    def test_wrong_answer_does_not_move_and_stores_nothing(self):
        self.start(branching_graph())
        response = self.act('w1', 'pick', answer='Emerald Fog')
        self.assertEqual(403, response.status_code)
        state = self.state()
        self.assertNotIn('graph_node_id', state)
        self.assertNotIn('graph_vars', state)
        self.assertEqual(0, ScoreLedger.objects.filter(session=self.session).count())
        self.assertEqual(0, SessionEvent.objects.filter(session=self.session).count())
        # The player is not locked out: a correct answer afterwards works.
        self.assertEqual(200, self.act('w2', 'pick', answer='Crimson Tide').status_code)

    def test_missing_answer_is_rejected_without_changes(self):
        self.start(branching_graph())
        response = self.act('m1', 'pick')
        self.assertEqual(400, response.status_code)
        self.assertNotIn('graph_vars', self.state())

    def test_client_cannot_create_or_change_session_variables(self):
        self.start(branching_graph())
        forged = {
            'palette': 'Azure Wave', 'graph_vars': {'palette': 'Azure Wave'}, 'variables': {'palette': 'Azure Wave'},
            'session_state': {'palette': 'Azure Wave', 'graph_node_id': 'finish'}, 'variable': 'palette',
            'equals': 'Azure Wave', 'condition': {'variable': 'palette', 'equals': 'Azure Wave'},
            'next_stage_id': 'blue-path', 'graph_node_id': 'finish', 'score': 9999,
        }
        # Forged data alone, without a valid answer, is rejected.
        self.assertEqual(400, self.act('f1', 'pick', **forged).status_code)
        # Forged data next to a WRONG answer changes nothing.
        self.assertEqual(403, self.act('f2', 'pick', answer='Emerald Fog', **forged).status_code)
        self.assertNotIn('graph_vars', self.state())
        # Forged data next to a VALID answer is ignored: the validated answer decides.
        response = self.act('f3', 'pick', answer='Crimson Tide', **forged)
        self.assertEqual(200, response.status_code)
        self.assertEqual('red-path', response.json()['next_stage_id'])
        self.assertEqual(100, response.json()['score'])
        self.assertEqual({'palette': 'Crimson Tide'}, self.state()['graph_vars'])
        self.assertEqual('red-path', self.state()['graph_node_id'])

    def test_raw_session_state_keys_cannot_satisfy_a_condition(self):
        # The old runtime read conditions from the whole session_state. Only graph_vars counts now.
        graph = {
            'nodes': [{'id': 'free'}, {'id': 'left'}, {'id': 'finish'}],
            'edges': [{'id': 'e1', 'source': 'free', 'target': 'left', 'condition': {'variable': 'palette', 'equals': 'Crimson Tide'}},
                      {'id': 'e2', 'source': 'left', 'target': 'finish'}],
            'start': 'free', 'end': 'finish',
        }
        self.start(graph, validate=False)  # no tool declares 'palette', so this graph could not even be published
        state = self.state()
        state['palette'] = 'Crimson Tide'
        self.session.session_state = state
        self.session.save(update_fields=['session_state'])
        response = self.act('r1', 'free')
        self.assertEqual(409, response.status_code)
        self.assertEqual(0, ScoreLedger.objects.filter(session=self.session).count())

    def test_replayed_event_does_not_run_again_even_with_a_different_answer(self):
        self.start(branching_graph())
        first = self.act('same-event', 'pick', answer='Crimson Tide')
        self.assertEqual(200, first.status_code)
        replay = self.act('same-event', 'pick', answer='Azure Wave')
        self.assertEqual(first.json(), replay.json())
        self.assertEqual('red-path', replay.json()['next_stage_id'])
        self.assertEqual({'palette': 'Crimson Tide'}, self.state()['graph_vars'])
        self.assertEqual('red-path', self.state()['graph_node_id'])
        self.assertEqual(1, ScoreLedger.objects.filter(session=self.session).count())
        self.assertEqual(1, SessionEvent.objects.filter(session=self.session).count())

    def test_no_response_reveals_answers_variables_or_session_state(self):
        self.start(branching_graph())
        bodies = [
            self.act('l1', 'pick'),                                # missing answer
            self.act('l2', 'pick', answer='Emerald Fog'),          # wrong answer
            self.act('l3', 'pick', answer='crimson tide'),         # correct answer
            self.act('l3', 'pick', answer='crimson tide'),         # replay
            self.act('l4', 'pick', answer='Azure Wave'),           # stale stage after moving on
        ]
        for response in bodies:
            text = response.content.decode().casefold()
            for secret in ['crimson tide', 'azure wave', 'palette', 'graph_vars', 'session_state', 'graph_node_id']:
                self.assertNotIn(secret, text, response.content)

    def test_variables_persist_so_a_later_node_can_branch_on_an_earlier_answer(self):
        graph = {
            'nodes': [
                {'id': 'pick', 'tools': [{'type': 'quiz', 'settings': {'answer': list(ACCEPTED), 'variable': 'palette'}}]},
                {'id': 'story'}, {'id': 'red-end'}, {'id': 'blue-end'}, {'id': 'finish'},
            ],
            'edges': [
                {'id': 'e0', 'source': 'pick', 'target': 'story'},
                {'id': 'e1', 'source': 'story', 'target': 'red-end', 'condition': {'variable': 'palette', 'equals': 'Crimson Tide'}},
                {'id': 'e2', 'source': 'story', 'target': 'blue-end', 'condition': {'variable': 'palette', 'equals': 'Azure Wave'}},
                {'id': 'e3', 'source': 'red-end', 'target': 'finish'}, {'id': 'e4', 'source': 'blue-end', 'target': 'finish'},
            ],
            'start': 'pick', 'end': 'finish',
        }
        self.start(graph)
        self.assertEqual('story', self.act('s1', 'pick', answer='Azure Wave').json()['next_stage_id'])
        self.assertEqual('blue-end', self.act('s2', 'story').json()['next_stage_id'])
        self.assertEqual({'palette': 'Azure Wave'}, self.state()['graph_vars'])

    def test_text_tool_with_accepted_list_is_case_sensitive_and_branches(self):
        tool = {'type': 'text_input', 'settings': {'exact': ['Crimson Tide', 'Azure Wave'], 'variable': 'palette'}}
        self.start(branching_graph(tool))
        self.assertEqual(403, self.act('t1', 'pick', text='crimson tide').status_code)  # Text stays case-sensitive
        self.assertNotIn('graph_vars', self.state())
        response = self.act('t2', 'pick', text='  Azure Wave ')
        self.assertEqual('blue-path', response.json()['next_stage_id'])
        self.assertEqual({'palette': 'Azure Wave'}, self.state()['graph_vars'])

    def test_free_text_without_exact_stores_nothing_and_follows_the_plain_edge(self):
        graph = {
            'nodes': [{'id': 'note', 'tools': [{'type': 'text', 'settings': {'max_length': 50}}]}, {'id': 'finish'}],
            'edges': [{'id': 'e1', 'source': 'note', 'target': 'finish'}], 'start': 'note', 'end': 'finish',
        }
        self.start(graph)
        response = self.act('x1', 'note', text='anything the player likes')
        self.assertEqual(200, response.status_code)
        self.assertEqual('finish', response.json()['next_stage_id'])
        self.assertNotIn('graph_vars', self.state())

    def test_nodes_without_conditions_behave_as_before(self):
        graph = {
            'nodes': [{'id': 'a', 'tools': [{'type': 'quiz', 'settings': {'answer': '42'}}]}, {'id': 'b'}, {'id': 'c'}],
            'edges': [{'id': 'e1', 'source': 'a', 'target': 'b'}, {'id': 'e2', 'source': 'b', 'target': 'c'}],
            'start': 'a', 'end': 'c',
        }
        self.start(graph)
        self.assertEqual(403, self.act('p1', 'a', answer='41').status_code)
        self.assertEqual('b', self.act('p2', 'a', answer=' 42 ').json()['next_stage_id'])
        self.assertEqual(409, self.act('p3', 'a', answer='42').status_code)  # no skipping back
        last = self.act('p4', 'b')
        self.assertEqual('c', last.json()['next_stage_id'])
        self.assertEqual(100, last.json()['score'])
        self.assertEqual({'a.quiz': '42'}, self.state()['graph_vars'])  # default variable name

    def test_hint_operation_stores_no_variables(self):
        tool_hint = {'type': 'hint', 'settings': {'content': 'Look up', 'score_cost': 3}}
        graph = branching_graph()
        graph['nodes'][0]['tools'].append(tool_hint)
        self.start(graph)
        self.assertEqual(200, self.act('h1', 'pick', operation='hint', answer='Crimson Tide').status_code)
        self.assertNotIn('graph_vars', self.state())
        self.assertNotIn('graph_node_id', self.state())


class ConditionalEdgeSafetyTests(GraphRuntimeBase):
    """Runtime behaviour on graphs the publish validation would reject (older or hand-edited data)."""

    def test_no_matching_edge_is_a_clean_conflict_with_nothing_recorded(self):
        graph = branching_graph()
        graph['edges'][0]['condition'] = {'variable': 'palette', 'equals': 'Something Else'}
        graph['edges'][1]['condition'] = {'variable': 'palette', 'equals': 'Nothing Matches'}
        self.start(graph, validate=False)
        response = self.act('n1', 'pick', answer='Crimson Tide')
        self.assertEqual(409, response.status_code)
        state = self.state()
        self.assertNotIn('graph_node_id', state)
        self.assertNotIn('graph_vars', state)  # the validated answer is not kept when the move is refused
        self.assertEqual(0, ScoreLedger.objects.filter(session=self.session).count())
        self.assertEqual(0, SessionEvent.objects.filter(session=self.session).count())

    def test_more_than_one_matching_edge_is_a_clean_conflict_with_nothing_recorded(self):
        graph = branching_graph()
        graph['edges'][1]['condition'] = {'variable': 'palette', 'equals': 'Crimson Tide'}  # duplicates the first
        self.start(graph, validate=False)
        response = self.act('a1', 'pick', answer='Crimson Tide')
        self.assertEqual(409, response.status_code)
        state = self.state()
        self.assertNotIn('graph_node_id', state)
        self.assertNotIn('graph_vars', state)
        self.assertEqual(0, ScoreLedger.objects.filter(session=self.session).count())

    def test_unsupported_condition_shapes_fail_closed(self):
        for bad in ({'variable': 'palette', 'not_equals': 'x'}, {'variable': 'palette', 'equals': 'Crimson Tide', 'also': 1}, 'palette=Crimson Tide', {'equals': 'Crimson Tide'}):
            graph = branching_graph()
            graph['edges'][0]['condition'] = bad
            graph['edges'][1]['condition'] = bad
            self.start(graph, validate=False)
            self.assertEqual(409, self.act('u1', 'pick', answer='Crimson Tide').status_code, bad)
            self.assertNotIn('graph_node_id', self.state())


class GraphConditionPublishValidationTests(TestCase):
    def errors(self, graph):
        return GameVersion.validate_graph(definition_with(graph))

    def test_valid_branching_graph_has_no_errors(self):
        self.assertEqual([], self.errors(branching_graph()))

    def test_existing_style_single_answer_graphs_stay_valid(self):
        graph = {'nodes': [{'id': 'a', 'tools': [{'type': 'quiz', 'settings': {'answer': '42'}}, {'type': 'text_input', 'settings': {'exact': 'urban'}}]}, {'id': 'b'}],
                 'edges': [{'id': 'e', 'source': 'a', 'target': 'b'}], 'start': 'a', 'end': 'b'}
        self.assertEqual([], self.errors(graph))

    def test_condition_on_an_undeclared_variable_is_rejected(self):
        graph = branching_graph()
        graph['edges'][0]['condition']['variable'] = 'nobody-sets-this'
        self.assertTrue(any('no quiz or text tool declares' in error for error in self.errors(graph)))

    def test_equals_outside_the_accepted_answers_is_rejected(self):
        graph = branching_graph()
        graph['edges'][0]['condition']['equals'] = 'Emerald Fog'
        self.assertTrue(any('must be one of the accepted answers' in error for error in self.errors(graph)))

    def test_non_string_equals_is_rejected(self):
        graph = branching_graph()
        graph['edges'][0]['condition']['equals'] = ['Crimson Tide']
        self.assertTrue(any('must be one of the accepted answers' in error for error in self.errors(graph)))

    def test_variable_set_only_after_the_edge_is_rejected(self):
        graph = {
            'nodes': [{'id': 'a'}, {'id': 'b', 'tools': [{'type': 'quiz', 'settings': {'answer': ['x', 'y'], 'variable': 'late'}}]}, {'id': 'c'}, {'id': 'd'}],
            'edges': [{'id': 'e1', 'source': 'a', 'target': 'b'},
                      {'id': 'e2', 'source': 'a', 'target': 'c', 'condition': {'variable': 'late', 'equals': 'x'}},
                      {'id': 'e3', 'source': 'b', 'target': 'd'}, {'id': 'e4', 'source': 'c', 'target': 'd'}],
            'start': 'a', 'end': 'd',
        }
        self.assertTrue(any('only set after this node' in error for error in self.errors(graph)))

    def test_mixing_conditional_and_unconditional_edges_is_rejected(self):
        graph = branching_graph()
        graph['nodes'].append({'id': 'extra'})
        graph['edges'].append({'id': 'e-extra', 'source': 'pick', 'target': 'extra'})
        graph['edges'].append({'id': 'e-extra-end', 'source': 'extra', 'target': 'finish'})
        self.assertTrue(any('mixes conditional and unconditional' in error for error in self.errors(graph)))

    def test_two_edges_with_the_same_condition_are_rejected(self):
        graph = branching_graph()
        graph['edges'][1]['condition'] = {'variable': 'palette', 'equals': 'Crimson Tide'}
        self.assertTrue(any('has two outgoing edges' in error for error in self.errors(graph)))

    def test_accepted_answer_without_a_path_is_rejected(self):
        graph = branching_graph()
        graph['nodes'].remove({'id': 'blue-path'})
        graph['edges'] = [edge for edge in graph['edges'] if 'blue' not in edge['id']]
        self.assertTrue(any("no outgoing edge for accepted answer 'Azure Wave'" in error for error in self.errors(graph)))

    def test_unsupported_or_incomplete_conditions_are_rejected(self):
        for bad in ({'variable': 'palette', 'not_equals': 'x'}, {'equals': 'Crimson Tide'}, {'variable': 'palette'}, 'palette=Crimson Tide'):
            graph = branching_graph()
            graph['edges'][0]['condition'] = bad
            self.assertTrue(self.errors(graph), bad)

    def test_answer_declarations_are_checked(self):
        empty = branching_graph({'type': 'quiz', 'settings': {'answer': [], 'variable': 'palette'}})
        self.assertTrue(any('empty or unsupported' in error for error in self.errors(empty)))
        duplicate = branching_graph({'type': 'quiz', 'settings': {'answer': ['Red', ' red '], 'variable': 'palette'}})
        self.assertTrue(any('more than once' in error for error in self.errors(duplicate)))
        # Text compares case-sensitively, so 'Red' and 'red' are two different accepted answers.
        text_ok = {'nodes': [{'id': 'a', 'tools': [{'type': 'text', 'settings': {'exact': ['Red', 'red']}}]}, {'id': 'b'}],
                   'edges': [{'id': 'e', 'source': 'a', 'target': 'b'}], 'start': 'a', 'end': 'b'}
        self.assertEqual([], self.errors(text_ok))

    def test_a_variable_name_cannot_be_declared_twice(self):
        graph = {
            'nodes': [{'id': 'a', 'tools': [{'type': 'quiz', 'settings': {'answer': 'x', 'variable': 'dup'}}]},
                      {'id': 'b', 'tools': [{'type': 'text', 'settings': {'exact': 'y', 'variable': 'dup'}}]}],
            'edges': [{'id': 'e', 'source': 'a', 'target': 'b'}], 'start': 'a', 'end': 'b',
        }
        self.assertTrue(any('declared by more than one tool' in error for error in self.errors(graph)))

    def test_publish_refuses_an_unusable_graph_and_accepts_the_fixed_one(self):
        city = City.objects.create(slug='validation-city', name='Validation')
        game = Game.objects.create(name='Validation', slug='validation-game', city=city)
        graph = branching_graph()
        graph['edges'][0]['condition']['equals'] = 'Emerald Fog'
        broken = GameVersion.objects.create(game=game, created='2026-01-01T00:00:00Z', definition=json.dumps(definition_with(graph)))
        published, errors = broken.publish()
        self.assertFalse(published)
        self.assertTrue(errors)
        fixed = GameVersion.objects.create(game=game, created='2026-01-02T00:00:00Z', definition=json.dumps(definition_with(branching_graph())))
        self.assertTrue(fixed.publish()[0])


class AnswerHelperTests(TestCase):
    def test_accepted_answers_shapes(self):
        self.assertIsNone(accepted_answers({}, 'quiz'))
        self.assertIsNone(accepted_answers(None, 'text'))
        self.assertEqual(['42'], accepted_answers({'answer': 42}, 'quiz'))
        self.assertEqual(['a', 'b'], accepted_answers({'answer': ['a', 'b']}, 'quiz'))
        self.assertEqual(['x'], accepted_answers({'exact': 'x'}, 'text'))
        self.assertEqual([], accepted_answers({'answer': []}, 'quiz'))
        self.assertEqual([], accepted_answers({'answer': [{'a': 1}]}, 'quiz'))
        self.assertEqual([], accepted_answers({'answer': True}, 'quiz'))

    def test_match_answer_rules(self):
        self.assertEqual('Azure Wave', match_answer('quiz', ' azure wave ', ['Crimson Tide', 'Azure Wave']))
        self.assertIsNone(match_answer('quiz', 'azure', ['Azure Wave']))
        self.assertEqual('north', match_answer('text', ' north ', ['north', 'south']))
        self.assertIsNone(match_answer('text', 'North', ['north', 'south']))
        self.assertEqual('42', match_answer('quiz', 42, ['42']))

    def test_answer_key_and_variable_names(self):
        self.assertEqual('red', answer_key('quiz', ' Red '))
        self.assertEqual(' Red ', answer_key('text', ' Red '))
        self.assertEqual('node-1.quiz', tool_variable_name('node-1', {'settings': {}}, 'quiz'))
        self.assertEqual('node-1.text', tool_variable_name('node-1', {'settings': None}, 'text'))
        self.assertEqual('palette', tool_variable_name('node-1', {'settings': {'variable': ' palette '}}, 'quiz'))
        self.assertEqual('node-1.quiz', tool_variable_name('node-1', {'settings': {'variable': '  '}}, 'quiz'))


# =============================== Second review round ===============================


def chain_graph(condition):
    """a --(condition)--> b -> c. Used to prove an invalid condition is never 'always true'."""
    edge = {'id': 'e1', 'source': 'a', 'target': 'b'}
    if condition is not ABSENT:
        edge['condition'] = condition
    return {'nodes': [{'id': 'a'}, {'id': 'b'}, {'id': 'c'}], 'edges': [edge, {'id': 'e2', 'source': 'b', 'target': 'c'}],
            'start': 'a', 'end': 'c'}


ABSENT = object()
INVALID_CONDITIONS = [
    {}, [], '', 0, False, 'palette=Crimson Tide', ['variable', 'equals'], 5,
    {'variable': '', 'equals': 'x'}, {'variable': 5, 'equals': 'x'}, {'variable': None, 'equals': 'x'},
    {'variable': 'palette'}, {'equals': 'x'}, {'key': 'palette'},
    {'variable': 'palette', 'key': 'palette', 'equals': 'Crimson Tide'},   # variable AND key together
    {'variable': 'palette', 'equals': 'x', 'extra': 1}, {'variable': 'palette', 'not_equals': 'x'},
]


class ConditionContractTests(GraphRuntimeBase):
    """One contract for publish validation and runtime: only None means 'no condition'."""

    def test_parse_condition_classification(self):
        self.assertEqual((CONDITION_NONE, None, None), parse_condition(None))
        self.assertEqual((CONDITION_VALID, 'palette', 'x'), parse_condition({'variable': 'palette', 'equals': 'x'}))
        self.assertEqual((CONDITION_VALID, 'palette', 'x'), parse_condition({'key': 'palette', 'equals': 'x'}))
        self.assertEqual((CONDITION_VALID, 'palette', None), parse_condition({'variable': 'palette', 'equals': None}))
        for bad in INVALID_CONDITIONS:
            self.assertEqual((CONDITION_INVALID, None, None), parse_condition(bad), bad)

    def test_invalid_conditions_are_rejected_at_publish(self):
        for bad in INVALID_CONDITIONS:
            errors = GameVersion.validate_graph(definition_with(chain_graph(bad)))
            self.assertTrue(any('invalid condition' in error for error in errors), (bad, errors))

    def test_invalid_conditions_are_never_treated_as_always_true_at_runtime(self):
        for bad in INVALID_CONDITIONS:
            self.start(chain_graph(bad), validate=False)  # unvalidated: the runtime must still fail closed
            response = self.act('c1', 'a')
            self.assertEqual(409, response.status_code, (bad, response.content))
            self.assertNotIn('graph_node_id', self.state(), bad)
            self.assertEqual(0, ScoreLedger.objects.filter(session=self.session).count(), bad)
            self.assertEqual(0, SessionEvent.objects.filter(session=self.session).count(), bad)

    def test_no_condition_keeps_working_whether_absent_or_null(self):
        for graph in (chain_graph(ABSENT), chain_graph(None)):
            self.assertEqual([], GameVersion.validate_graph(definition_with(graph)))
            self.start(graph)
            self.assertEqual('b', self.act('n1', 'a').json()['next_stage_id'])
            self.assertEqual('c', self.act('n2', 'b').json()['next_stage_id'])

    def test_key_alias_alone_is_valid_and_routes(self):
        graph = branching_graph()
        for edge in graph['edges'][:2]:
            edge['condition'] = {'key': edge['condition']['variable'], 'equals': edge['condition']['equals']}
        self.assertEqual([], GameVersion.validate_graph(definition_with(graph)))
        self.start(graph)
        self.assertEqual('blue-path', self.act('k1', 'pick', answer='Azure Wave').json()['next_stage_id'])

    def test_variable_and_key_together_is_rejected_by_publish_and_runtime(self):
        graph = branching_graph()
        for edge in graph['edges'][:2]:
            edge['condition'] = {'variable': 'palette', 'key': 'palette', 'equals': edge['condition']['equals']}
        self.assertTrue(any('invalid condition' in error for error in GameVersion.validate_graph(definition_with(graph))))
        self.start(graph, validate=False)
        self.assertEqual(409, self.act('vk1', 'pick', answer='Crimson Tide').status_code)
        self.assertNotIn('graph_node_id', self.state())
        self.assertNotIn('graph_vars', self.state())


class DominatorTests(TestCase):
    def doms(self, edges, start='s', nodes=None):
        nodes = nodes or sorted({n for pair in edges for n in pair} | {start})
        return graph_dominators(set(nodes), [{'source': a, 'target': b} for a, b in edges], start)

    def test_chain(self):
        self.assertEqual({'s': {'s'}, 'a': {'s', 'a'}, 'b': {'s', 'a', 'b'}}, self.doms([('s', 'a'), ('a', 'b')]))

    def test_diamond_branch_nodes_do_not_dominate_the_merge(self):
        doms = self.doms([('s', 'a'), ('s', 'b'), ('a', 'm'), ('b', 'm')])
        self.assertEqual({'s', 'm'}, doms['m'])
        self.assertEqual({'s', 'a'}, doms['a'])

    def test_unreachable_nodes_are_left_out_and_do_not_weaken_reachable_ones(self):
        doms = self.doms([('s', 'a'), ('x', 'a')], nodes=['s', 'a', 'x'])
        self.assertNotIn('x', doms)
        self.assertEqual({'s', 'a'}, doms['a'])

    def test_cycles_terminate(self):
        doms = self.doms([('s', 'a'), ('a', 'b'), ('b', 'a')])
        self.assertEqual({'s', 'a'}, doms['a'])
        self.assertEqual({'s', 'a', 'b'}, doms['b'])

    def test_unknown_start_gives_nothing(self):
        self.assertEqual({}, graph_dominators({'a'}, [], 'missing'))


class BranchingStructureValidationTests(TestCase):
    def errors(self, graph):
        return GameVersion.validate_graph(definition_with(graph))

    @staticmethod
    def fork_graph(merge_variable='route', declare_extra_on='a'):
        """fork(route: left|right) -> a | b -> merge -> x | y -> end. 'extra' is set on one branch only."""
        a_tools = [{'type': 'quiz', 'settings': {'answer': ['p', 'q'], 'variable': 'extra'}}] if declare_extra_on == 'a' else []
        merge_values = {'route': ('left', 'right'), 'extra': ('p', 'q')}[merge_variable]
        return {
            'nodes': [
                {'id': 'fork', 'tools': [{'type': 'quiz', 'settings': {'answer': ['left', 'right'], 'variable': 'route'}}]},
                {'id': 'a', 'tools': a_tools}, {'id': 'b'}, {'id': 'merge'}, {'id': 'x'}, {'id': 'y'}, {'id': 'end'},
            ],
            'edges': [
                {'id': 'f-a', 'source': 'fork', 'target': 'a', 'condition': {'variable': 'route', 'equals': 'left'}},
                {'id': 'f-b', 'source': 'fork', 'target': 'b', 'condition': {'variable': 'route', 'equals': 'right'}},
                {'id': 'a-m', 'source': 'a', 'target': 'merge'}, {'id': 'b-m', 'source': 'b', 'target': 'merge'},
                {'id': 'm-x', 'source': 'merge', 'target': 'x', 'condition': {'variable': merge_variable, 'equals': merge_values[0]}},
                {'id': 'm-y', 'source': 'merge', 'target': 'y', 'condition': {'variable': merge_variable, 'equals': merge_values[1]}},
                {'id': 'x-e', 'source': 'x', 'target': 'end'}, {'id': 'y-e', 'source': 'y', 'target': 'end'},
            ],
            'start': 'fork', 'end': 'end',
        }

    def test_variable_set_on_only_one_path_to_the_branching_node_is_rejected(self):
        errors = self.errors(self.fork_graph(merge_variable='extra'))
        self.assertTrue(any("Node 'merge'" in error and 'not guaranteed to be set' in error for error in errors), errors)

    def test_variable_set_on_every_path_is_accepted_even_when_branching_node_is_further_down(self):
        # 'route' is set by 'fork', which lies on every path to 'merge' (a dominator), so this is valid.
        self.assertEqual([], self.errors(self.fork_graph(merge_variable='route', declare_extra_on=None)))

    def test_branching_far_below_the_declaring_node_still_needs_every_answer_covered(self):
        graph = self.fork_graph(merge_variable='route', declare_extra_on=None)
        graph['nodes'] = [node for node in graph['nodes'] if node['id'] != 'y']
        graph['edges'] = [edge for edge in graph['edges'] if edge['id'] not in ('m-y', 'y-e')]
        errors = self.errors(graph)
        self.assertTrue(any("Node 'merge' has no outgoing edge for accepted answer 'right'" in error for error in errors), errors)

    def test_next_node_with_an_accepted_answer_that_has_no_edge_is_rejected(self):
        graph = {
            'nodes': [{'id': 'pick', 'tools': [{'type': 'quiz', 'settings': {'answer': list(ACCEPTED), 'variable': 'palette'}}]},
                      {'id': 'story'}, {'id': 'mid'}, {'id': 'finish'}],
            'edges': [{'id': 'e0', 'source': 'pick', 'target': 'story'},
                      {'id': 'e1', 'source': 'story', 'target': 'mid', 'condition': {'variable': 'palette', 'equals': 'Crimson Tide'}},
                      {'id': 'e2', 'source': 'mid', 'target': 'finish'}],
            'start': 'pick', 'end': 'finish',
        }
        errors = self.errors(graph)
        self.assertTrue(any("Node 'story' has no outgoing edge for accepted answer 'Azure Wave'" in error for error in errors), errors)

    def test_two_variables_on_the_outgoing_edges_of_one_node_are_rejected(self):
        graph = {
            'nodes': [{'id': 'n1', 'tools': [{'type': 'quiz', 'settings': {'answer': ['a', 'b'], 'variable': 'v1'}}]},
                      {'id': 'n2', 'tools': [{'type': 'quiz', 'settings': {'answer': ['c', 'd'], 'variable': 'v2'}}]},
                      {'id': 'n3'}, {'id': 't1'}, {'id': 't2'}, {'id': 'end'}],
            'edges': [{'id': 'e1', 'source': 'n1', 'target': 'n2'}, {'id': 'e2', 'source': 'n2', 'target': 'n3'},
                      {'id': 'e3', 'source': 'n3', 'target': 't1', 'condition': {'variable': 'v1', 'equals': 'a'}},
                      {'id': 'e4', 'source': 'n3', 'target': 't2', 'condition': {'variable': 'v2', 'equals': 'c'}},
                      {'id': 'e5', 'source': 't1', 'target': 'end'}, {'id': 'e6', 'source': 't2', 'target': 'end'}],
            'start': 'n1', 'end': 'end',
        }
        errors = self.errors(graph)
        self.assertTrue(any("Node 'n3' branches on more than one variable (v1, v2)" in error for error in errors), errors)

    def test_branching_variable_must_not_be_undeclared_even_with_a_second_variable(self):
        graph = branching_graph()
        graph['edges'][0]['condition']['variable'] = 'nobody-sets-this'
        errors = self.errors(graph)
        self.assertTrue(any('no quiz or text tool declares' in error for error in errors), errors)
        self.assertTrue(any('more than one variable' in error for error in errors), errors)


class OverlapRuntimeTests(GraphRuntimeBase):
    def test_overlapping_two_variable_edges_return_conflict_and_record_nothing(self):
        graph = {
            'nodes': [{'id': 'n1', 'tools': [{'type': 'quiz', 'settings': {'answer': ['a', 'b'], 'variable': 'v1'}}]},
                      {'id': 'n2', 'tools': [{'type': 'quiz', 'settings': {'answer': ['c', 'd'], 'variable': 'v2'}}]},
                      {'id': 'n3'}, {'id': 't1'}, {'id': 't2'}, {'id': 'end'}],
            'edges': [{'id': 'e1', 'source': 'n1', 'target': 'n2'}, {'id': 'e2', 'source': 'n2', 'target': 'n3'},
                      {'id': 'e3', 'source': 'n3', 'target': 't1', 'condition': {'variable': 'v1', 'equals': 'a'}},
                      {'id': 'e4', 'source': 'n3', 'target': 't2', 'condition': {'variable': 'v2', 'equals': 'c'}},
                      {'id': 'e5', 'source': 't1', 'target': 'end'}, {'id': 'e6', 'source': 't2', 'target': 'end'}],
            'start': 'n1', 'end': 'end',
        }
        self.start(graph, validate=False)  # publish would reject this graph
        self.assertEqual('n2', self.act('o1', 'n1', answer='a').json()['next_stage_id'])
        self.assertEqual('n3', self.act('o2', 'n2', answer='c').json()['next_stage_id'])
        before = ScoreLedger.objects.filter(session=self.session).count()
        response = self.act('o3', 'n3')
        self.assertEqual(409, response.status_code)  # both e3 and e4 match: refuse to guess
        self.assertEqual('n3', self.state()['graph_node_id'])
        self.assertEqual(before, ScoreLedger.objects.filter(session=self.session).count())


class DiamondRuntimeTests(GraphRuntimeBase):
    def test_a_branch_taken_early_is_remembered_at_the_merge(self):
        graph = BranchingStructureValidationTests.fork_graph(merge_variable='route', declare_extra_on=None)
        self.assertEqual([], GameVersion.validate_graph(definition_with(graph)))
        self.start(graph)
        self.assertEqual('b', self.act('d1', 'fork', answer='right').json()['next_stage_id'])
        self.assertEqual('merge', self.act('d2', 'b').json()['next_stage_id'])
        self.assertEqual('y', self.act('d3', 'merge').json()['next_stage_id'])


class UnknownOperationTests(GraphRuntimeBase):
    """An unknown operation must never skip the Quiz/Text/Timer checks that guard a transition."""

    def gate_graph(self):
        return {
            'nodes': [{'id': 'gate', 'tools': [
                {'type': 'hint', 'settings': {'content': 'Say the magic words', 'score_cost': 2}},
                {'type': 'timer', 'settings': {'minimum_seconds': 60}},
                {'type': 'quiz', 'settings': {'answer': 'open sesame', 'variable': 'word'}},
            ]}, {'id': 'end'}],
            'edges': [{'id': 'e', 'source': 'gate', 'target': 'end'}], 'start': 'gate', 'end': 'end',
        }

    BYPASS_ATTEMPTS = ['skip', 'finish', 'next', 'advance', 'COMPLETE', 'Complete', 'complete ', ' complete', '', 'completed',
                       'complete_node', 'pass', None, 5, True, ['complete'], {'op': 'complete'}]

    def test_unknown_operations_cannot_skip_quiz_or_timer(self):
        self.start(self.gate_graph())
        for index, operation in enumerate(self.BYPASS_ATTEMPTS):
            for extra in ({}, {'answer': 'wrong'}, {'answer': 'open sesame'}):
                response = self.act('bypass-%d-%d' % (index, len(extra)), 'gate', operation=operation, **extra)
                self.assertEqual(400, response.status_code, (operation, extra, response.content))
        state = self.state()
        self.assertEqual({}, state)  # nothing moved, no timer started, no variable stored
        self.assertEqual(0, ScoreLedger.objects.filter(session=self.session).count())
        self.assertEqual(0, SessionEvent.objects.filter(session=self.session).count())

    def test_unknown_operation_cannot_skip_a_conditional_branch_either(self):
        self.start(branching_graph())
        for index, operation in enumerate(['skip', 'finish', None, ['complete']]):
            self.assertEqual(400, self.act('b%d' % index, 'pick', operation=operation, answer='Crimson Tide').status_code)
        self.assertEqual({}, self.state())

    def test_complete_still_requires_the_answer_and_the_timer(self):
        self.start(self.gate_graph())
        # The timer is checked first (existing order): until it has elapsed nothing else is reached.
        self.assertEqual(409, self.act('c1', 'gate').status_code)
        self.assertEqual(409, self.act('c2', 'gate', answer='open sesame').status_code)
        state = self.state()
        state['graph_timer_started_at:gate'] = time.time() - 120
        self.session.session_state = state
        self.session.save(update_fields=['session_state'])
        self.assertEqual(400, self.act('c3', 'gate').status_code)                                  # answer is mandatory
        self.assertEqual(403, self.act('c4', 'gate', answer='wrong').status_code)
        self.assertEqual({'graph_timer_started_at:gate': state['graph_timer_started_at:gate']}, self.state())
        self.assertEqual(200, self.act('c5', 'gate', operation='complete', answer='open sesame').status_code)
        self.assertEqual({'word': 'open sesame'}, self.state()['graph_vars'])

    def test_explicit_complete_and_omitted_operation_are_equivalent(self):
        self.start(chain_graph(ABSENT))
        self.assertEqual('b', self.act('e1', 'a', operation='complete').json()['next_stage_id'])
        self.assertEqual('c', self.act('e2', 'b').json()['next_stage_id'])

    def test_hint_keeps_its_behaviour(self):
        self.start(self.gate_graph())
        response = self.act('h1', 'gate', operation='hint')
        self.assertEqual(200, response.status_code)
        self.assertEqual('Say the magic words', response.json()['content'])
        self.assertEqual(-2, ScoreLedger.objects.get(session=self.session).amount)
        self.assertNotIn('graph_vars', self.state())

    def test_hint_and_media_without_the_tool_are_still_refused_as_before(self):
        self.start(chain_graph(ABSENT))
        self.assertEqual(403, self.act('m1', 'a', operation='hint').status_code)
        self.assertEqual(403, self.act('m2', 'a', operation='media').status_code)


# =============================== Third review round ===============================


def cond(value):
    return {'variable': 'palette', 'equals': value}


def fan_graph(conditions, node_tools=None):
    """Node 'a' with one outgoing edge per entry of ``conditions`` (ABSENT = no condition key)."""
    tools = node_tools if node_tools is not None else [{'type': 'quiz', 'settings': {'answer': list(ACCEPTED), 'variable': 'palette'}}]
    nodes = [{'id': 'a', 'tools': tools}, {'id': 'end'}]
    edges = []
    for index, condition in enumerate(conditions):
        target = 't%d' % index
        nodes.append({'id': target})
        edge = {'id': 'e%d' % index, 'source': 'a', 'target': target}
        if condition is not ABSENT:
            edge['condition'] = condition
        edges.append(edge)
        edges.append({'id': 'x%d' % index, 'source': target, 'target': 'end'})
    return {'nodes': nodes, 'edges': edges, 'start': 'a', 'end': 'end'}


# Structures of ONE node's outgoing edges that the rules forbid. For answer 'Crimson Tide' the
# old runtime would still have chosen a path in several of these (exactly one edge matched).
MALFORMED_BRANCHES = {
    'empty dict plus unconditional': [{}, ABSENT],
    'empty list plus unconditional': [[], ABSENT],
    'empty string plus unconditional': ['', ABSENT],
    'zero plus unconditional': [0, ABSENT],
    'false plus unconditional': [False, ABSENT],
    'variable and key plus unconditional': [{'variable': 'palette', 'key': 'palette', 'equals': 'Crimson Tide'}, ABSENT],
    'unknown key plus unconditional': [{'variable': 'palette', 'equals': 'Crimson Tide', 'extra': 1}, ABSENT],
    'missing equals plus unconditional': [{'variable': 'palette'}, ABSENT],
    'valid but false plus unconditional': [cond('Azure Wave'), ABSENT],
    'valid and true plus unconditional': [cond('Crimson Tide'), ABSENT],
    'true edge plus invalid edge': [cond('Crimson Tide'), {'variable': 'palette', 'key': 'palette', 'equals': 'Azure Wave'}],
    'true edge plus empty dict edge': [cond('Crimson Tide'), {}],
    'only invalid conditions': [{}, []],
    'two variables, only one true': [cond('Crimson Tide'), {'variable': 'other', 'equals': 'x'}],
}


class RuntimeBranchStructureTests(GraphRuntimeBase):
    """The runtime applies the publish-time branching rules to the CURRENT node before it moves."""

    def assert_refused_and_nothing_recorded(self, response, label, expected_state=None):
        self.assertEqual(409, response.status_code, (label, response.content))
        self.assertEqual(expected_state if expected_state is not None else {}, self.state(), label)
        self.assertEqual(0, ScoreLedger.objects.filter(session=self.session).count(), label)
        self.assertEqual(0, SessionEvent.objects.filter(session=self.session).count(), label)

    def test_malformed_branches_are_refused_without_choosing_a_path(self):
        for label, conditions in MALFORMED_BRANCHES.items():
            self.start(fan_graph(conditions), validate=False)
            response = self.act('s1', 'a', answer='Crimson Tide')
            self.assert_refused_and_nothing_recorded(response, label)

    def test_runtime_and_publish_agree_on_every_malformed_branch(self):
        for label, conditions in MALFORMED_BRANCHES.items():
            graph = fan_graph(conditions)
            self.assertTrue(branch_problems(graph['edges'][::2]), label)  # the shared rule itself
            self.assertTrue(GameVersion.validate_graph(definition_with(graph)), label)  # publish refuses it
            self.start(graph, validate=False)
            self.assertEqual(409, self.act('s1', 'a', answer='Crimson Tide').status_code, label)  # runtime refuses it

    def test_the_old_runtime_would_have_moved_on_these(self):
        # Documents why the check exists: exactly one edge matches in each of these, so the
        # candidate rule alone (one match = go) would pick that edge.
        for label in ('empty dict plus unconditional', 'valid but false plus unconditional',
                      'true edge plus invalid edge', 'two variables, only one true'):
            graph = fan_graph(MALFORMED_BRANCHES[label])
            self.start(graph, validate=False)
            node_edges = graph['edges'][::2]
            matching = [edge for edge in node_edges if _graph_condition_matches(edge.get('condition'), {'palette': 'Crimson Tide'})]
            self.assertEqual(1, len(matching), label)
            self.assertEqual(409, self.act('s1', 'a', answer='Crimson Tide').status_code, label)

    def test_malformed_branch_refuses_before_the_timer_starts_or_an_answer_is_kept(self):
        tools = [{'type': 'timer', 'settings': {'minimum_seconds': 1}},
                 {'type': 'quiz', 'settings': {'answer': list(ACCEPTED), 'variable': 'palette'}}]
        self.start(fan_graph([{}, ABSENT], node_tools=tools), validate=False)
        response = self.act('s1', 'a', answer='Crimson Tide')
        self.assert_refused_and_nothing_recorded(response, 'timer node')  # state stays {}: no timer key, no variable

    def _two_variable_graph(self):
        return {
            'nodes': [{'id': 'n1', 'tools': [{'type': 'quiz', 'settings': {'answer': ['a', 'b'], 'variable': 'v1'}}]},
                      {'id': 'n2', 'tools': [{'type': 'quiz', 'settings': {'answer': ['c', 'd'], 'variable': 'v2'}}]},
                      {'id': 'n3'}, {'id': 't1'}, {'id': 't2'}, {'id': 'end'}],
            'edges': [{'id': 'e1', 'source': 'n1', 'target': 'n2'}, {'id': 'e2', 'source': 'n2', 'target': 'n3'},
                      {'id': 'e3', 'source': 'n3', 'target': 't1', 'condition': {'variable': 'v1', 'equals': 'a'}},
                      {'id': 'e4', 'source': 'n3', 'target': 't2', 'condition': {'variable': 'v2', 'equals': 'c'}},
                      {'id': 'e5', 'source': 't1', 'target': 'end'}, {'id': 'e6', 'source': 't2', 'target': 'end'}],
            'start': 'n1', 'end': 'end',
        }

    def test_two_variables_are_refused_whether_one_or_both_conditions_hold(self):
        graph = self._two_variable_graph()
        self.assertTrue(any('more than one variable (v1, v2)' in error for error in GameVersion.validate_graph(definition_with(graph))))
        for first, second, label in (('a', 'd', 'only v1 true'), ('b', 'c', 'only v2 true'), ('a', 'c', 'both true'), ('b', 'd', 'none true')):
            self.start(graph, validate=False)
            self.assertEqual(200, self.act('v1', 'n1', answer=first).status_code)
            self.assertEqual(200, self.act('v2', 'n2', answer=second).status_code)
            before_vars = self.state()['graph_vars']
            ledger_before = ScoreLedger.objects.filter(session=self.session).count()
            events_before = SessionEvent.objects.filter(session=self.session).count()
            response = self.act('v3', 'n3')
            self.assertEqual(409, response.status_code, (label, response.content))
            self.assertEqual('n3', self.state()['graph_node_id'], label)
            self.assertEqual(before_vars, self.state()['graph_vars'], label)
            self.assertEqual(ledger_before, ScoreLedger.objects.filter(session=self.session).count(), label)
            self.assertEqual(events_before, SessionEvent.objects.filter(session=self.session).count(), label)

    def test_hint_and_media_on_a_malformed_node_keep_their_behaviour(self):
        tools = [{'type': 'hint', 'settings': {'content': 'Look closer', 'score_cost': 1}},
                 {'type': 'quiz', 'settings': {'answer': list(ACCEPTED), 'variable': 'palette'}}]
        self.start(fan_graph([{}, ABSENT], node_tools=tools), validate=False)
        hint = self.act('h1', 'a', operation='hint')
        self.assertEqual(200, hint.status_code)
        self.assertEqual('Look closer', hint.json()['content'])
        self.assertEqual(403, self.act('m1', 'a', operation='media').status_code)  # no media tool: unchanged

    def test_well_formed_nodes_are_not_affected(self):
        self.start(branching_graph())
        self.assertEqual('red-path', self.act('w1', 'pick', answer='Crimson Tide').json()['next_stage_id'])
        self.start(chain_graph(ABSENT))
        self.assertEqual('b', self.act('w2', 'a').json()['next_stage_id'])

    def test_branch_problems_codes(self):
        edges = lambda *conditions: [dict({'id': 'e%d' % i}, **({} if c is ABSENT else {'condition': c})) for i, c in enumerate(conditions)]
        self.assertEqual([], branch_problems(edges()))
        self.assertEqual([], branch_problems(edges(ABSENT, None)))
        self.assertEqual([], branch_problems(edges(cond('x'), cond('y'))))
        self.assertEqual([(BRANCH_MIXED, None)], branch_problems(edges(cond('x'), ABSENT)))
        self.assertEqual([(BRANCH_INVALID_CONDITION, 'e0')], branch_problems(edges({})))
        self.assertEqual([(BRANCH_MIXED, None), (BRANCH_INVALID_CONDITION, 'e0')], branch_problems(edges([], ABSENT)))
        self.assertEqual([(BRANCH_MULTIPLE_VARIABLES, ['other', 'palette'])],
                         branch_problems(edges(cond('x'), {'variable': 'other', 'equals': 'y'})))


class TextAnswerUsabilityTests(GraphRuntimeBase):
    """A Text answer the player could never enter must not be publishable, and the stored value
    is exactly the text the player's answer was compared with."""

    UNUSABLE = [
        ('', 'empty'), ('   ', 'only spaces'), ('\t\n', 'only whitespace characters'),
        (' north', 'leading space'), ('north ', 'trailing space'), (' north ', 'both'),
        (['north', ''], 'list with an empty member'), (['north', '   '], 'list with a space-only member'),
        (['north', ' south'], 'list member with a leading space'), (['north', 'south '], 'list member with a trailing space'),
    ]

    @staticmethod
    def text_graph(exact, **settings):
        return {'nodes': [{'id': 'a', 'tools': [{'type': 'text_input', 'settings': dict({'exact': exact}, **settings)}]}, {'id': 'b'}],
                'edges': [{'id': 'e', 'source': 'a', 'target': 'b'}], 'start': 'a', 'end': 'b'}

    def test_accepted_answers_for_text(self):
        for exact, label in self.UNUSABLE:
            self.assertEqual([], accepted_answers({'exact': exact}, 'text'), label)
        self.assertEqual(['north'], accepted_answers({'exact': 'north'}, 'text'))
        self.assertEqual(['north', 'south'], accepted_answers({'exact': ['north', 'south']}, 'text'))
        self.assertEqual(['north east'], accepted_answers({'exact': 'north east'}, 'text'))  # inner spaces are fine
        self.assertEqual(['42', '4.5'], accepted_answers({'exact': [42, 4.5]}, 'text'))

    def test_text_answers_must_fit_max_length(self):
        self.assertEqual([], accepted_answers({'exact': 'abcdef', 'max_length': 5}, 'text'))
        self.assertEqual(['abcde'], accepted_answers({'exact': 'abcde', 'max_length': 5}, 'text'))
        self.assertEqual([], accepted_answers({'exact': 'x' * 1001}, 'text'))  # default limit is 1000
        self.assertEqual(['x' * 1000], accepted_answers({'exact': 'x' * 1000}, 'text'))
        self.assertEqual([], accepted_answers({'exact': ['ok', 'toolong'], 'max_length': 5}, 'text'))
        self.assertEqual(['north'], accepted_answers({'exact': 'north', 'max_length': 0}, 'text'))  # 0 means default, as in the runtime
        self.assertEqual(['north'], accepted_answers({'exact': 'north', 'max_length': 'abc'}, 'text'))  # unreadable limit: not judged here

    def test_unusable_text_answers_cannot_be_published(self):
        for exact, label in self.UNUSABLE:
            errors = GameVersion.validate_graph(definition_with(self.text_graph(exact)))
            self.assertTrue(any('empty or unsupported' in error for error in errors), (label, errors))
        errors = GameVersion.validate_graph(definition_with(self.text_graph('abcdef', max_length=5)))
        self.assertTrue(any('empty or unsupported' in error for error in errors), errors)

    def test_usable_text_answers_still_publish(self):
        for exact in ('north', ['north', 'south'], 'north east', 42):
            self.assertEqual([], GameVersion.validate_graph(definition_with(self.text_graph(exact))), exact)

    def test_unusable_text_answer_in_a_branching_graph_cannot_be_published(self):
        tool = {'type': 'text', 'settings': {'exact': ['Crimson Tide', ' Azure Wave'], 'variable': 'palette'}}
        errors = GameVersion.validate_graph(definition_with(branching_graph(tool)))
        self.assertTrue(any('empty or unsupported' in error for error in errors), errors)

    def test_match_answer_for_text_is_exact_and_never_matches_empty_text(self):
        self.assertIsNone(match_answer('text', '', ['']))
        self.assertIsNone(match_answer('text', '   ', ['   ', 'north']))
        self.assertIsNone(match_answer('text', 'north', [' north ']))
        self.assertIsNone(match_answer('text', 'North', ['north']))
        for sent in ('north', '  north', 'north  ', '\tnorth\n'):
            matched = match_answer('text', sent, ['north', 'south'])
            self.assertEqual('north', matched)
            self.assertEqual(sent.strip(), matched)  # canonical value == the text it was compared with

    def test_quiz_behaviour_is_unchanged(self):
        self.assertEqual([' 42 '], accepted_answers({'answer': ' 42 '}, 'quiz'))
        self.assertEqual(' 42 ', match_answer('quiz', '42', [' 42 ']))
        self.assertEqual('Azure Wave', match_answer('quiz', ' azure WAVE ', ['Crimson Tide', 'Azure Wave']))
        self.assertEqual([''], accepted_answers({'answer': ''}, 'quiz'))

    def test_stored_value_is_exactly_the_players_stripped_text(self):
        tool = {'type': 'text', 'settings': {'exact': ['Crimson Tide', 'Azure Wave'], 'variable': 'palette'}}
        self.start(branching_graph(tool))
        response = self.act('t1', 'pick', text='   Azure Wave \n')
        self.assertEqual('blue-path', response.json()['next_stage_id'])
        self.assertEqual({'palette': 'Azure Wave'}, self.state()['graph_vars'])

    def test_older_published_definitions_with_dead_text_answers_fail_closed(self):
        for exact in (' north ', 'north ', '   ', ''):
            self.start(self.text_graph(exact), validate=False)  # published before the rule existed
            for sent in ('north', ' north ', ' ', ''):
                response = self.act('d-%s' % repr(sent), 'a', text=sent)
                self.assertIn(response.status_code, (400, 403), (exact, sent, response.content))
            self.assertNotIn('graph_node_id', self.state(), exact)
            self.assertNotIn('graph_vars', self.state(), exact)
            self.assertEqual(0, ScoreLedger.objects.filter(session=self.session).count(), exact)
