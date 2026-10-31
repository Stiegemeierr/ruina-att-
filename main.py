"""Cliente Ruina. Protocolo Flutter candidato ate validacao autenticada."""
import json
import os
import re
import unicodedata
from argparse import ArgumentParser
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import pytz
import requests
import yaml

BASE_URL = 'https://portal.ufsm.br/mobile/webservice/flutter'
WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
MEALS = {'coffee': 1, 'lunch': 2, 'dinner': 3}
TIMEOUT = (10, 30)


class RuinaError(Exception):
    """Falha apresentada sem corpo HTTP ou credenciais."""


def response_shape(value, depth=0):
    """Somente tipos, campos conhecidos e flags; nunca valores textuais."""
    if depth > 3:
        return type(value).__name__
    if isinstance(value, dict):
        known = {'error', 'body', 'mensagem', 'message', 'sucesso', 'dataRefAgendada',
                 'tipoRefeicao', 'impedimento', 'accessToken', 'token', 'status', 'errors',
                 'id', 'item', 'itemId', 'descricao'}
        return {'type': 'dict', 'fields': {key: response_shape(item, depth + 1)
                for key, item in value.items() if key in known},
                'other_fields': sum(key not in known for key in value)}
    if isinstance(value, list):
        return {'type': 'list', 'count': len(value), 'sample': [response_shape(item, depth + 1) for item in value[:2]]}
    if type(value) is bool or value is None:
        return value
    return type(value).__name__


def read_config(path):
    try:
        config = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, yaml.YAMLError):
        raise RuinaError('Nao foi possivel ler a configuracao YAML.') from None
    if not isinstance(config, dict) or not isinstance(config.get('environment'), dict):
        raise RuinaError('Configuracao deve conter environment e schedules.')
    for key in ('app', 'device-id', 'device-info', 'message-token'):
        if not isinstance(config['environment'].get(key), str) or not config['environment'][key].strip():
            raise RuinaError(f'environment.{key} deve ser um texto nao vazio.')
    if not isinstance(config.get('schedules'), list):
        raise RuinaError('schedules deve ser uma lista.')
    unique = set()
    for index, schedule in enumerate(config['schedules'], 1):
        if not isinstance(schedule, dict) or schedule.get('weekday') not in WEEKDAYS:
            raise RuinaError(f'Agenda {index}: weekday invalido.')
        restaurant = schedule.get('restaurant')
        if type(restaurant) is not int or restaurant <= 0:
            raise RuinaError(f'Agenda {index}: restaurant deve ser inteiro positivo.')
        for key in ('vegetarian', *MEALS):
            if type(schedule.get(key)) is not bool:
                raise RuinaError(f'Agenda {index}: {key} deve ser true ou false.')
        if not any(schedule[key] for key in MEALS):
            raise RuinaError(f'Agenda {index}: selecione pelo menos uma refeicao.')
        for meal in MEALS:
            if schedule[meal]:
                identity = (schedule['weekday'], MEALS[meal])
                if identity in unique:
                    raise RuinaError(f'Agenda {index}: refeicao duplicada no mesmo dia.')
                unique.add(identity)
    return config


def find_schedules(config, target):
    return [s for s in config['schedules'] if s['weekday'] == WEEKDAYS[target.weekday()]]


def select_schedules(schedules, restaurant=None, meal=None):
    selected = []
    for schedule in schedules:
        if restaurant is not None and schedule['restaurant'] != restaurant:
            continue
        if meal is not None:
            if not schedule[meal]:
                continue
            schedule = dict(schedule)
            for key in MEALS:
                schedule[key] = key == meal
        selected.append(schedule)
    return selected


def build_payload(target, options):
    stamp = target.strftime('%Y-%m-%d') + ' 00:00:00'
    return {
        'dataInicio': stamp, 'dataFim': stamp,
        'idRestaurante': 41 if options['restaurant'] == 2 else options['restaurant'],
        'opcaoVegetariana': options['vegetarian'],
        'tiposRefeicoes': [item for key, item in MEALS.items() if options[key]],
    }


class Client:
    def __init__(self, environment):
        self.environment = environment
        self.secrets = [str(value) for value in environment.values() if isinstance(value, str)]

    def rejection_message(self, value):
        if not isinstance(value, str):
            return ''
        message = value
        for secret in sorted(set(self.secrets), key=len, reverse=True):
            if secret:
                message = message.replace(secret, '[REMOVIDO]')
        message = re.sub(r'(?i)Bearer\s+\S+', 'Bearer [REMOVIDO]', message)
        message = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[REMOVIDO]', message)
        message = ''.join(char if char.isprintable() else ' ' for char in message)
        return message[:500].strip()

    def post(self, route, payload, token=None):
        headers = {
            'User-Agent': 'Dart/3.12 (dart:io)',
            'x-ufsm-version': '50600',
            'Content-Type': 'application/json; charset=UTF-8',
        }
        if token:
            self.secrets.append(token)
            headers.update({'X-UFSM-Device-ID': self.environment['device-id'],
                            'Authorization': f'Bearer {token}'})
        try:
            response = requests.post(BASE_URL + route, json=payload, headers=headers,
                                     timeout=TIMEOUT, allow_redirects=False)
            response.raise_for_status()
        except requests.Timeout as error:
            phase = 'conexao' if isinstance(error, requests.ConnectTimeout) else 'resposta'
            action = ('Nenhuma reserva foi enviada; falhou somente o login.' if route == '/generateTokenJwt'
                      else 'Confira no app antes de repetir: a reserva pode ter ocorrido.')
            raise RuinaError(f'Tempo esgotado aguardando {phase} da UFSM. {action}') from None
        except requests.RequestException:
            raise RuinaError('Falha HTTP ou de conexao com a UFSM. Confira o app antes de repetir uma reserva.') from None
        if not 200 <= response.status_code < 300:
            raise RuinaError('Resposta HTTP inesperada; redirecionamentos nao sao seguidos.')
        if 'application/json' not in response.headers.get('Content-Type', '').lower():
            raise RuinaError('Resposta da UFSM nao tem Content-Type application/json.')
        try:
            data = response.json()
        except ValueError:
            raise RuinaError('Resposta da UFSM nao e JSON valido.') from None
        if not isinstance(data, dict) or data.get('error') is not False or 'body' not in data:
            operation = 'login' if route == '/generateTokenJwt' else 'reserva'
            print(f'DIAGNOSTICO: operacao={operation}; HTTP={response.status_code}; '
                  + json.dumps(response_shape(data), ensure_ascii=True))
            if isinstance(data, dict) and data.get('error') is True:
                reason = self.rejection_message(data.get('body'))
                detail = f' Motivo informado pela UFSM: {reason}' if reason else ' Consulte o app oficial.'
                raise RuinaError(f'UFSM informou error=true na operacao de {operation}.' + detail)
            raise RuinaError(f'Formato inesperado na resposta de {operation}. '
                             'Se houve pedido de reserva, confira no app antes de repetir.')
        return data['body']

    def login(self, username, password):
        self.secrets.extend((username, password))
        env = self.environment
        body = self.post('/generateTokenJwt', {
            'appName': env['app'], 'deviceId': env['device-id'],
            'deviceInfo': env['device-info'], 'messageToken': env['message-token'],
            'login': username, 'senha': password,
        })
        if not isinstance(body, dict) or not isinstance(body.get('accessToken'), str) or not body['accessToken'].strip():
            raise RuinaError('Login nao retornou um accessToken valido.')
        return body['accessToken']

    def schedule(self, token, payload):
        body = self.post('/ru/agendaRefeicoes', payload, token)
        try:
            return validate_statuses(body, payload)
        except RuinaError:
            print('DIAGNOSTICO: operacao=confirmacao; '
                  + json.dumps(response_shape(body), ensure_ascii=True))
            print('A reserva pode ter sido criada. Confira no app antes de repetir o pedido.')
            raise


def meal_id(value):
    if type(value) is int and value in MEALS.values():
        return value
    if isinstance(value, dict):
        identifiers = []
        for key in ('id', 'item', 'itemId'):
            if key in value:
                if type(value[key]) is not int or value[key] not in MEALS.values():
                    raise RuinaError('Identificador de refeicao inesperado; confira no app.')
                identifiers.append(value[key])
        if 'descricao' in value:
            if not isinstance(value['descricao'], str):
                raise RuinaError('Descricao de refeicao inesperada; confira no app.')
            identifiers.append(meal_id(value['descricao']))
        if not identifiers or len(set(identifiers)) != 1:
            raise RuinaError('Objeto de refeicao sem identificacao unica; confira no app.')
        return identifiers[0]
    if not isinstance(value, str):
        raise RuinaError('Tipo de refeicao inesperado na confirmacao.')
    normalized = ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c)).strip().lower()
    names = {'cafe': 1, 'cafe da manha': 1, 'desjejum': 1, 'almoco': 2, 'janta': 3, 'jantar': 3}
    if normalized not in names:
        raise RuinaError('Tipo de refeicao desconhecido; confira a reserva no app.')
    return names[normalized]


def validate_statuses(statuses, payload):
    if not isinstance(statuses, list) or not statuses:
        raise RuinaError('Reserva sem confirmacoes; confira no app antes de repetir.')
    received = []
    for status in statuses:
        if not isinstance(status, dict) or type(status.get('sucesso')) is not bool:
            raise RuinaError('Confirmacao em formato inesperado; confira no app.')
        try:
            actual_date = datetime.strptime(status['dataRefAgendada'], '%Y-%m-%d %H:%M:%S').date()
        except (KeyError, TypeError, ValueError):
            raise RuinaError('Data de confirmacao invalida; confira no app.') from None
        if actual_date.isoformat() != payload['dataInicio'][:10]:
            raise RuinaError('Confirmacao recebida para outra data; confira no app.')
        received.append(meal_id(status.get('tipoRefeicao')))
    if Counter(received) != Counter(payload['tiposRefeicoes']):
        raise RuinaError('Confirmacoes incompletas, duplicadas ou inesperadas; confira no app.')
    if not all(s['sucesso'] for s in statuses):
        raise RuinaError('Uma ou mais refeicoes foram recusadas. Consulte o impedimento no app oficial.')
    return statuses


def main(argv=None):
    parser = ArgumentParser(description='Agenda refeicoes do RU da UFSM. Padrao: simulacao sem rede.')
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.yaml'))
    parser.add_argument('--date', type=date.fromisoformat, help='Dia da refeicao: AAAA-MM-DD. Padrao: amanha em Brasilia.')
    parser.add_argument('--restaurant', type=int, help='Filtra pelo numero do RU na configuracao (1 ou 2, por exemplo).')
    parser.add_argument('--meal', choices=tuple(MEALS), help='Filtra uma refeicao: coffee, lunch ou dinner.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Mostra pedidos sem acessar a UFSM (padrao).')
    mode.add_argument('--auth-check', action='store_true', help='Testa somente login; nao reserva.')
    mode.add_argument('--reserve', action='store_true', help='Envia reservas reais para a agenda selecionada.')
    parser.add_argument('-u', '--username', help='Prefira a variavel UFSM_USERNAME.')
    parser.add_argument('-p', '--password', help='Prefira a variavel UFSM_PASSWORD.')
    args = parser.parse_args(argv)
    try:
        config = read_config(args.config)
        target = args.date or (datetime.now(pytz.timezone('America/Sao_Paulo')).date() + timedelta(days=1))
        schedules = select_schedules(find_schedules(config, target), args.restaurant, args.meal)
        if not args.reserve and not args.auth_check:
            print('SIMULACAO: nenhuma chamada a UFSM; protocolo candidato, ainda nao validado com conta.')
            print(json.dumps({'date': target.isoformat(), 'requests': [build_payload(target, s) for s in schedules]}, ensure_ascii=False, indent=2))
            return 0
        if args.reserve and not schedules:
            print(f'SEM AGENDA: {target.isoformat()}; nenhuma chamada a UFSM.')
            return 0
        username = args.username or os.environ.get('UFSM_USERNAME', '')
        password = args.password or os.environ.get('UFSM_PASSWORD', '')
        if not username.strip() or not password.strip():
            raise RuinaError('Defina UFSM_USERNAME e UFSM_PASSWORD antes do teste autenticado.')
        client = Client(config['environment'])
        token = client.login(username, password)
        if args.auth_check:
            print('LOGIN VALIDADO: token recebido; nenhuma reserva solicitada.')
            return 0
        print('LOGIN VALIDADO: enviando somente as reservas selecionadas.')
        for schedule in schedules:
            client.schedule(token, build_payload(target, schedule))
            print(f"CONFIRMADO PELA API: {target.isoformat()}, RU {schedule['restaurant']}. Confira tambem no app.")
        return 0
    except RuinaError as error:
        print(f'ERRO: {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
