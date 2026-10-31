"""Teste interativo de login; nunca cria reservas nem salva a senha."""
from getpass import getpass
from importlib.util import find_spec
from pathlib import Path
import sys

# A investigacao instala dependencias isoladas em work/deps. Tambem aceita
# .deps ao lado do script, sem depender de PYTHONPATH no terminal do usuario.
if any(find_spec(name) is None for name in ('requests', 'yaml', 'pytz')):
    script = Path(__file__).resolve()
    candidates = (script.parent / '.deps', script.parents[2] / 'work' / 'deps')
    for candidate in candidates:
        if all((candidate / name / '__init__.py').is_file() for name in ('requests', 'yaml', 'pytz')):
            sys.path.insert(0, str(candidate))
            break

if any(find_spec(name) is None for name in ('requests', 'yaml', 'pytz')):
    print('Dependencias ausentes. Instale com:')
    print(f'python -m pip install -r "{Path(__file__).with_name("requirements.txt")}"')
    raise SystemExit(1)

from main import main


def check_login():
    try:
        username = input('Matricula UFSM: ').strip()
        password = getpass('Senha UFSM (nao sera exibida): ')
    except (EOFError, KeyboardInterrupt):
        print('\nTeste cancelado.')
        return 1
    return main(['--auth-check', '--username', username, '--password', password])


if __name__ == '__main__':
    raise SystemExit(check_login())
