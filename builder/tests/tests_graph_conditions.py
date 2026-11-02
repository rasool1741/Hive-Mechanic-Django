import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ..models import City, Game, GameVersion, Player, ScoreLedger, Session, SessionEvent
from ..urban_tools import accepted_answers, answer_key, match_answer, tool_variable_name

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
