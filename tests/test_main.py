import contextlib
import copy
import io
import os
import subprocess
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import yaml
import main

CONFIG_PATH = Path(main.__file__).with_name('config.yaml')
DATE = date(2026, 11, 3)


def response(body, error=False):
    result = Mock(status_code=200, headers={'Content-Type': 'application/json; charset=UTF-8'})
    result.json.return_value = {'error': error, 'body': body}
    return result


def confirmation(meal, success=True, day='2026-11-03'):
    return {'tipoRefeicao': meal, 'dataRefAgendada': day + ' 00:00:00', 'sucesso': success}


class RuinaTests(unittest.TestCase):
    def setUp(self):
        self.config = main.read_config(CONFIG_PATH)
        self.client = main.Client(self.config['environment'])
        self.options = main.find_schedules(self.config, DATE)[0]
        self.payload = main.build_payload(DATE, self.options)

    def cli(self, args, replies=(), env=None):
        output = io.StringIO()
        with patch.dict(os.environ, env or {}, clear=True), \
             patch('requests.post', side_effect=replies) as post, \
             contextlib.redirect_stdout(output):
            code = main.main(args)
        return code, output.getvalue(), post

    def check_invalid_config(self, config):
        with patch.object(Path, 'read_text', return_value=yaml.safe_dump(config)), self.assertRaises(main.RuinaError):
            main.read_config(CONFIG_PATH)

    def test_default_mode_never_accesses_network(self):
        code, log, post = self.cli(['--date', DATE.isoformat()])
        self.assertEqual(code, 0)
        self.assertIn('SIMULACAO', log)
        self.assertIn('2026-11-03 00:00:00', log)
        post.assert_not_called()

    def test_dry_run_does_not_print_environment_or_credentials(self):
        code, log, post = self.cli(['--dry-run'], env={'UFSM_USERNAME': 'private-user', 'UFSM_PASSWORD': 'private-password'})
        self.assertEqual(code, 0)
        for private in ('private-user', 'private-password', self.config['environment']['message-token']):
            self.assertNotIn(private, log)
        post.assert_not_called()

    def test_full_week(self):
        for day in range(2, 9):
            target = date(2026, 11, day)
            schedules = main.find_schedules(self.config, target)
            self.assertEqual(len(schedules), 2 if target.weekday() < 5 else 0)

    def test_restaurants_meals_and_midnight(self):
        schedules = main.find_schedules(self.config, DATE)
        payloads = [main.build_payload(DATE, s) for s in schedules]
        self.assertEqual([p['idRestaurante'] for p in payloads], [41, 1])
        self.assertEqual([p['tiposRefeicoes'] for p in payloads], [[2], [1, 3]])
        for payload in payloads:
            self.assertEqual(payload['dataInicio'], '2026-11-03 00:00:00')
            self.assertEqual(payload['dataInicio'], payload['dataFim'])

    def test_single_meal_filter_does_not_modify_config(self):
        original = copy.deepcopy(self.config)
        schedules = main.select_schedules(main.find_schedules(self.config, DATE), 1, 'dinner')
        self.assertEqual(len(schedules), 1)
        self.assertEqual(main.build_payload(DATE, schedules[0])['tiposRefeicoes'], [3])
        self.assertEqual(self.config, original)
        self.assertEqual(main.select_schedules(main.find_schedules(self.config, DATE), 2, 'coffee'), [])

    def test_single_meal_reservation_sends_only_selected_request(self):
        code, _, post = self.cli(['--reserve', '--date', DATE.isoformat(), '--restaurant', '1',
                                  '--meal', 'dinner', '-u', 'user', '-p', 'password'],
                                 [response({'accessToken': 'token'}), response([confirmation('Jantar')])])
        self.assertEqual(code, 0)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.kwargs['json']['tiposRefeicoes'], [3])

    def test_interactive_login_only_checks_authentication(self):
        import testar_login
        with patch('builtins.input', return_value='user'), patch('testar_login.getpass', return_value='password'), \
             patch('testar_login.main', return_value=0) as run:
            self.assertEqual(testar_login.check_login(), 0)
            self.assertEqual(run.call_args.args[0], ['--auth-check', '--username', 'user', '--password', 'password'])

    def test_login_launcher_works_without_pythonpath(self):
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        result = subprocess.run([sys.executable, str(CONFIG_PATH.with_name('testar_login.py'))],
                                input='', capture_output=True, text=True, env=env, timeout=15)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Matricula UFSM:', result.stdout)
        self.assertIn('Teste cancelado', result.stdout)
        self.assertNotIn('Traceback', result.stderr)

    def test_interactive_reservation_preserves_single_selection(self):
        import testar_reserva
        with patch('builtins.input', return_value='user'), patch('testar_reserva.getpass', return_value='password'), \
             patch('testar_reserva.main', return_value=0) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(testar_reserva.reserve_one(['--date', '2026-11-03', '--restaurant', '1', '--meal', 'coffee']), 0)
        selection = ['--date', '2026-11-03', '--restaurant', '1', '--meal', 'coffee']
        self.assertEqual(run.call_args_list[0].args[0], ['--dry-run', *selection])
        self.assertEqual(run.call_args_list[1].args[0], ['--reserve', *selection, '--username', 'user', '--password', 'password'])

    def test_default_date_uses_brasilia_and_handles_rollovers(self):
        for current, expected in [(datetime(2026, 12, 31, 23, 59), '2027-01-01'),
                                  (datetime(2028, 2, 28, 23, 59), '2028-02-29'),
                                  (datetime(2026, 1, 31, 23, 59), '2026-02-01')]:
            with self.subTest(current=current), patch('main.datetime') as clock:
                clock.now.return_value = current
                code, log, _ = self.cli(['--dry-run'])
                self.assertEqual(code, 0)
                self.assertIn(expected, log)
                self.assertEqual(clock.now.call_args.args[0].zone, 'America/Sao_Paulo')

    def test_invalid_config_types_and_duplicates(self):
        mutations = [('weekday', 'Seg'), ('restaurant', True), ('vegetarian', 'false'), ('lunch', None)]
        for field, value in mutations:
            with self.subTest(field=field):
                config = copy.deepcopy(self.config)
                config['schedules'][0][field] = value
                self.check_invalid_config(config)
        config = copy.deepcopy(self.config)
        config['schedules'].append(copy.deepcopy(config['schedules'][0]))
        self.check_invalid_config(config)

    def test_empty_meals_and_missing_environment_are_rejected(self):
        config = copy.deepcopy(self.config)
        config['schedules'][0]['lunch'] = False
        self.check_invalid_config(config)
        config = copy.deepcopy(self.config)
        del config['environment']['app']
        self.check_invalid_config(config)

    def test_missing_file_and_malformed_yaml_fail_without_network(self):
        code, _, post = self.cli(['--config', 'missing-file.yaml'])
        self.assertEqual(code, 1)
        post.assert_not_called()
        with patch.object(Path, 'read_text', return_value='environment: ['):
            code, _, post = self.cli(['--config', 'bad.yaml'])
            self.assertEqual(code, 1)
            post.assert_not_called()

    def test_missing_credentials_fail_without_network(self):
        code, _, post = self.cli(['--auth-check'])
        self.assertEqual(code, 1)
        post.assert_not_called()

    def test_auth_check_only_logs_in_and_hides_token(self):
        code, log, post = self.cli(['--auth-check'], [response({'accessToken': 'private-token'})],
                                 {'UFSM_USERNAME': 'private-user', 'UFSM_PASSWORD': 'private-password'})
        self.assertEqual(code, 0)
        self.assertEqual(post.call_count, 1)
        self.assertTrue(post.call_args.args[0].endswith('/flutter/generateTokenJwt'))
        for value in ('private-user', 'private-password', 'private-token'):
            self.assertNotIn(value, log)

    def test_long_and_short_credentials_options_work(self):
        for username, password in [('--username', '--password'), ('-u', '-p')]:
            code, _, post = self.cli(['--auth-check', username, 'user', password, 'password'], [response({'accessToken': 'token'})])
            self.assertEqual(code, 0)
            self.assertEqual(post.call_args.kwargs['json']['login'], 'user')

    def test_login_rejected_or_missing_token_returns_failure(self):
        for result in [response({}, error=True), response({}), response({'accessToken': ''})]:
            code, log, post = self.cli(['--auth-check', '-u', 'user', '-p', 'password'], [result])
            self.assertEqual(code, 1)
            self.assertIn('ERRO', log)
            self.assertEqual(post.call_count, 1)

    def test_network_failures_are_not_retried_and_do_not_leak_exception(self):
        for failure in [requests.Timeout('private-token'), requests.ConnectionError('private-password'), requests.HTTPError('private-user')]:
            code, log, post = self.cli(['--auth-check', '-u', 'user', '-p', 'password'], [failure])
            self.assertEqual(code, 1)
            self.assertEqual(post.call_count, 1)
            self.assertNotIn('private-', log)

    def test_login_timeout_identifies_phase_and_no_reservation(self):
        for failure, phase in [(requests.ConnectTimeout(), 'conexao'), (requests.ReadTimeout(), 'resposta')]:
            code, log, post = self.cli(['--auth-check', '-u', 'user', '-p', 'password'], [failure])
            self.assertEqual(code, 1)
            self.assertIn(phase, log)
            self.assertIn('Nenhuma reserva foi enviada', log)
            self.assertEqual(post.call_count, 1)

    def test_status_and_content_type_and_json_are_checked(self):
        results = []
        http = response({})
        http.raise_for_status.side_effect = requests.HTTPError('sensitive body')
        results.append(http)
        redirect = response({})
        redirect.status_code = 302
        results.append(redirect)
        html = response({})
        html.headers = {'Content-Type': 'text/html'}
        results.append(html)
        bad_json = response({})
        bad_json.json.side_effect = ValueError('sensitive JSON')
        results.append(bad_json)
        for result in results:
            with patch('requests.post', return_value=result), self.assertRaises(main.RuinaError):
                self.client.login('user', 'password')

    def test_legacy_and_malformed_envelopes_are_rejected(self):
        for data in [[], None, {'token': 'legacy', 'error': False}, {'body': {}}, {'error': 0, 'body': {}}]:
            result = response({})
            result.json.return_value = data
            with patch('requests.post', return_value=result), self.assertRaises(main.RuinaError):
                self.client.login('user', 'password')

    def test_response_diagnostic_hides_text_and_unknown_keys(self):
        data = {'body': {'accessToken': 'private-token', 'mensagem': 'private-password',
                         'private-user': 'private-user'}, 'error': True}
        result = response({})
        result.json.return_value = data
        with patch('requests.post', return_value=result), contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(main.RuinaError, 'error=true'):
                self.client.login('user', 'password')
        self.assertIn('operacao=login', output.getvalue())
        self.assertNotIn('private-', output.getvalue())

    def test_rejection_reason_redacts_credentials_and_tokens(self):
        token = 'private-token'
        reason = 'Agendamento recusado para private-user; private-password; private-token'
        replies = [response({'accessToken': token}), response(reason, error=True)]
        code, log, post = self.cli(['--reserve', '--date', DATE.isoformat(), '--restaurant', '2', '--meal', 'lunch',
                                   '-u', 'private-user', '-p', 'private-password'], replies)
        self.assertEqual(code, 1)
        self.assertEqual(post.call_count, 2)
        self.assertIn('Motivo informado pela UFSM: Agendamento recusado', log)
        self.assertNotIn('private-', log)

    def test_rejection_reason_strips_control_characters_and_bearer(self):
        reason = self.client.rejection_message('Token Bearer other-secret\nfalhou\x1b')
        self.assertNotIn('other-secret', reason)
        self.assertNotIn('\n', reason)
        self.assertNotIn('\x1b', reason)

    def test_missing_error_diagnostic_identifies_reservation_format(self):
        result = response({})
        result.json.return_value = {'body': [confirmation('Almoço')]}
        with patch('requests.post', return_value=result), contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(main.RuinaError, 'Formato inesperado'):
                self.client.schedule('token', self.payload)
        self.assertIn('operacao=reserva', output.getvalue())
        self.assertIn('tipoRefeicao', output.getvalue())

    def test_full_reservation_uses_candidate_protocol_and_exact_meals(self):
        replies = [response({'accessToken': 'private-token'}), response([confirmation('Almoço')]),
                   response([confirmation('Café'), confirmation('Janta')])]
        code, log, post = self.cli(['--reserve', '--date', DATE.isoformat(), '-u', 'user', '-p', 'password'], replies)
        self.assertEqual(code, 0)
        self.assertEqual(post.call_count, 3)
        call = post.call_args_list[1]
        self.assertTrue(call.args[0].endswith('/flutter/ru/agendaRefeicoes'))
        self.assertEqual(call.kwargs['headers']['Authorization'], 'Bearer private-token')
        self.assertEqual(call.kwargs['json']['tiposRefeicoes'], [2])
        self.assertNotIn('private-token', log)
        for call in post.call_args_list:
            self.assertEqual(call.kwargs['timeout'], (10, 30))
            self.assertIs(call.kwargs['allow_redirects'], False)

    def test_reservation_without_agenda_skips_login(self):
        code, log, post = self.cli(['--reserve', '--date', '2026-11-07'])
        self.assertEqual(code, 0)
        self.assertIn('SEM AGENDA', log)
        post.assert_not_called()

    def test_failed_reservation_stops_remaining_requests(self):
        for failure in [response([]), response([confirmation('Almoço', False)]), requests.Timeout('ambiguous')]:
            code, _, post = self.cli(['--reserve', '--date', DATE.isoformat(), '-u', 'user', '-p', 'password'],
                                     [response({'accessToken': 'token'}), failure])
            self.assertEqual(code, 1)
            self.assertEqual(post.call_count, 2)

    def test_partial_extra_duplicate_or_wrong_date_confirmations_fail(self):
        payload = main.build_payload(DATE, main.find_schedules(self.config, DATE)[1])
        for statuses in [[confirmation('Café')], [confirmation('Café'), confirmation('Café')],
                         [confirmation('Café'), confirmation('Janta'), confirmation('Almoço')],
                         [confirmation('Café'), confirmation('Janta', day='2026-11-04')]]:
            with self.subTest(statuses=statuses), self.assertRaises(main.RuinaError):
                main.validate_statuses(statuses, payload)

    def test_malformed_confirmation_fields_fail(self):
        for statuses in [None, {}, [], [None], [{}], [confirmation('unknown')],
                         [dict(confirmation('Almoço'), sucesso='true')],
                         [dict(confirmation('Almoço'), dataRefAgendada='invalid')]]:
            with self.subTest(statuses=statuses), self.assertRaises(main.RuinaError):
                main.validate_statuses(statuses, self.payload)

    def test_structured_meal_identifiers(self):
        for value in [{'item': 1, 'itemId': 1, 'descricao': 'Café'}, {'id': 1},
                      {'descricao': 'Café da manhã'}, {'itemId': 1}]:
            with self.subTest(value=value):
                self.assertEqual(main.meal_id(value), 1)
        for value in [{}, {'id': True}, {'id': 1, 'itemId': 2}, {'id': 1, 'descricao': 'Janta'},
                      {'id': 99}, {'descricao': None}, {'item': [1]}]:
            with self.subTest(value=value), self.assertRaises(main.RuinaError):
                main.meal_id(value)

    def test_unknown_confirmation_emits_safe_diagnostic_without_retry(self):
        result = response([confirmation({'unexpected-secret-key': 'private-token'})])
        with patch('requests.post', return_value=result) as post, contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(main.RuinaError):
                self.client.schedule('private-token', self.payload)
        self.assertEqual(post.call_count, 1)
        self.assertIn('operacao=confirmacao', output.getvalue())
        self.assertIn('A reserva pode ter sido criada', output.getvalue())
        self.assertNotIn('private-token', output.getvalue())
        self.assertNotIn('unexpected-secret-key', output.getvalue())


if __name__ == '__main__':
    unittest.main()
