# Ruína

Agendamento automático de refeições do RU da UFSM, conforme a agenda em `config.yaml`.

Esta versão atualiza a integração com o UFSM Digital para autenticação JWT e corrige a leitura das confirmações. Também adiciona validação da configuração, tratamento de falhas, timeout, testes e simulação sem acesso à rede.

O programa simula por padrão (`python main.py`). Use `--auth-check` para testar somente o login e `--reserve` para enviar reservas reais. As credenciais são lidas das variáveis `UFSM_USERNAME` e `UFSM_PASSWORD`. Os scripts `testar_login.py` e `testar_reserva.py` permitem informá-las no terminal sem exibir a senha.

No GitHub Actions, configure as secrets `Username` e `Password`. O workflow roda às 06h17 de Brasília para preparar o dia seguinte; somente envia reservas quando a variável de repositório `RUINA_ENABLE_SCHEDULING` estiver definida como `true`.

Instale as dependências com `python -m pip install -r requirements.txt`. Execute os testes com `python -m unittest discover -s tests -v`.

Baseado no [Ruína de @jaimeadf](https://github.com/jaimeadf/ruina) e na proposta de migração de @eduswern-ui. Licença original preservada em `LICENSE`.
