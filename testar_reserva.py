"""Envia uma unica refeicao escolhida, com credenciais solicitadas no terminal."""
from argparse import ArgumentParser
from datetime import date
from getpass import getpass

# Reutiliza a localizacao das dependencias do teste de login.
from testar_login import main


def reserve_one(argv=None):
    parser = ArgumentParser(description='Teste real de uma unica refeicao. Confira depois no app oficial.')
    parser.add_argument('--date', required=True, type=date.fromisoformat)
    parser.add_argument('--restaurant', required=True, type=int, choices=(1, 2))
    parser.add_argument('--meal', required=True, choices=('coffee', 'lunch', 'dinner'))
    args = parser.parse_args(argv)
    selection = ['--date', args.date.isoformat(), '--restaurant', str(args.restaurant), '--meal', args.meal]
    code = main(['--dry-run', *selection])
    if code:
        return code
    print('RESERVA REAL: sera enviada somente a selecao acima. Confira antes se ela ja existe no app.')
    try:
        username = input('Matricula UFSM: ').strip()
        password = getpass('Senha UFSM (nao sera exibida): ')
    except (EOFError, KeyboardInterrupt):
        print('\nTeste cancelado; nenhuma reserva enviada.')
        return 1
    return main(['--reserve', *selection, '--username', username, '--password', password])


if __name__ == '__main__':
    raise SystemExit(reserve_one())
