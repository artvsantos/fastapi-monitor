# Monitor de Sites

[![CI](https://github.com/artvsantos/fastapi-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/artvsantos/fastapi-monitor/actions/workflows/ci.yml)

**Português** · [English](README.en.md)

API de monitoramento de sites (uptime) feita com FastAPI. Você cria uma
conta, cadastra os endereços que quer acompanhar e o intervalo de
checagem. Um worker checa cada site sozinho, guarda o status e o tempo de
resposta, e avisa por webhook quando o site cai ou volta. A API mostra o
histórico e a porcentagem de tempo no ar.

## O que ela faz

- Cadastro e login com JWT.
- Monitores só do dono, com intervalo de 1 minuto a 1 dia.
- Histórico de checagens com paginação por cursor.
- Estatísticas por período (24h, 7d, 30d): tempo no ar, tempo médio de
  resposta e número de quedas.
- Avisos por webhook quando o status muda, assinados com HMAC, com novas
  tentativas se o destino falhar.
- Limpeza automática do histórico com mais de 30 dias.
- Documentação interativa em `/docs`, com mensagens de erro em português.

## Decisões técnicas

**Proteção contra SSRF.** O servidor acessa URLs que qualquer pessoa
cadastra. Sem filtro, alguém cadastraria `http://169.254.169.254` (as
credenciais da nuvem) ou `http://postgres:5432` e usaria o servidor para
chegar à rede interna. O filtro tem duas camadas:

1. Ao cadastrar, recusa esquemas que não são http(s), usuário e senha na
   URL, `localhost` e IPs internos, e consulta o DNS para pegar domínios
   públicos que apontam para dentro da rede.
2. Ao checar, o DNS é consultado de novo **na conexão**: uma camada de rede
   própria (httpcore) resolve o nome, confere o endereço e conecta
   naquele IP. Isso fecha o *DNS rebinding*, quando o domínio responde um
   IP público no cadastro e um interno depois. Redirecionamentos não são
   seguidos, e o proxy do ambiente é ignorado.

O mesmo filtro vale para o endereço do webhook.

**O Postgres como fila, sem Redis.** Cada monitor guarda o horário da
próxima checagem. O worker pega os vencidos com
`SELECT ... FOR UPDATE SKIP LOCKED` e já agenda a próxima checagem na mesma
transação. Com isso, vários workers podem rodar juntos sem checar o mesmo
site duas vezes. Para este volume, o Redis seria mais uma peça para manter
sem ganho real.

**Avisos que não se perdem (padrão outbox).** O aviso é gravado na mesma
transação da checagem que mudou o status, e o envio vem depois. Se o
worker cair entre um e outro, o aviso continua na fila. Se o webhook
falhar, ele tenta de novo depois de 1, 2, 4 e 8 minutos. Os avisos de um
mesmo monitor saem na ordem: o "voltou" espera o "caiu" sair. A entrega
é "pelo menos uma vez", e por isso o corpo leva um `alert_id` para quem
recebe descartar repetidos.

**Estatísticas calculadas no banco.** Contagem, média e porcentagem saem
numa consulta só, sem trazer milhares de linhas para o Python. As quedas
são contadas com a função de janela `LAG()`: uma checagem fora do ar cuja
anterior estava no ar.

**Paginação por cursor.** O histórico usa `before=<data>` em vez de
`?page=2`. Assim, uma checagem nova não empurra itens de uma página para
outra, e a consulta usa o índice `(monitor_id, checked_at)` em qualquer
página, enquanto `OFFSET` leria e descartaria tudo o que vem antes.

**Senhas e tokens.** As senhas usam argon2, e o login com um e-mail que
não existe leva o mesmo tempo que um com senha errada, para não revelar
quem tem conta. O JWT exige `sub` e `exp` e só aceita o algoritmo
esperado. A API se recusa a subir num banco de verdade com a chave de
desenvolvimento.

## Rodando no seu computador

Com Docker, o comando abaixo sobe a API, o worker e o Postgres:

```bash
docker compose up --build
```

A documentação fica em http://localhost:8000/docs.

Sem Docker, com Python 3.13 ou mais novo (usa SQLite):

```bash
python -m venv venv
venv/bin/pip install -r requirements-dev.txt   # no Windows: venv\Scripts\pip
venv/bin/alembic upgrade head
RUN_WORKER_IN_API=1 venv/bin/uvicorn app.main:app --reload
```

## Exemplo de uso

```bash
API=http://localhost:8000

curl -X POST $API/auth/register -H 'Content-Type: application/json' \
  -d '{"email": "ana@exemplo.com", "password": "uma-senha-forte"}'

TOKEN=$(curl -s -X POST $API/auth/token \
  -d 'username=ana@exemplo.com&password=uma-senha-forte' | jq -r .access_token)

curl -X POST $API/monitors -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name": "Meu site", "url": "https://exemplo.com.br/",
       "interval_minutes": 5, "webhook_url": "https://meu-servidor.com.br/avisos"}'

curl $API/monitors/1/stats?period=7d -H "Authorization: Bearer $TOKEN"
```

```json
{
  "period": "7d",
  "since": "2026-10-20T12:00:00Z",
  "checks": 2016,
  "up_checks": 2010,
  "uptime_percent": 99.7,
  "avg_response_ms": 182,
  "incidents": 2
}
```

## Webhook

Quando o site cai ou volta, o endereço cadastrado recebe um `POST`:

```json
{
  "event": "down",
  "alert_id": 42,
  "message": "Meu site (https://exemplo.com.br/) caiu: O site respondeu com o código 503.",
  "monitor": {"id": 1, "name": "Meu site", "url": "https://exemplo.com.br/"},
  "check": {
    "checked_at": "2026-10-27T12:00:00+00:00",
    "status_code": 503,
    "response_time_ms": 241,
    "error": "O site respondeu com o código 503."
  }
}
```

O aviso conta como entregue quando a resposta é 2xx. Cada monitor tem um
`webhook_secret`, e a assinatura vai no cabeçalho `X-Monitor-Signature`.
O horário entra na conta, para que ninguém reenvie depois um aviso antigo
que tenha capturado. Para conferir:

```python
import hashlib
import hmac
import time


def aviso_valido(segredo: str, headers: dict, corpo: bytes) -> bool:
    timestamp = headers['X-Monitor-Timestamp']
    if abs(time.time() - int(timestamp)) > 300:  # mais de 5 minutos
        return False
    esperado = hmac.new(
        segredo.encode(), f'{timestamp}.'.encode() + corpo, hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(headers['X-Monitor-Signature'],
                               f'sha256={esperado}')
```

## Configuração

Tudo vem de variáveis de ambiente (ou de um arquivo `.env`):

| Variável | Padrão | Para quê |
| --- | --- | --- |
| `DATABASE_URL` | SQLite local | Aceita a URL do Postgres como o Neon mostra. |
| `SECRET_KEY` | só para desenvolvimento | Assina os tokens. Obrigatória (32+ caracteres) com Postgres. |
| `RUN_WORKER_IN_API` | `false` | Roda o worker dentro da API, para hospedagens com um processo só. |
| `WORKER_INTERVAL_SECONDS` | `10` | Pausa entre as rodadas do worker. |
| `WORKER_CONCURRENCY` | `10` | Checagens ao mesmo tempo. |
| `CHECK_TIMEOUT` | `10` | Segundos de espera por site. |
| `CHECK_RETENTION_DAYS` | `30` | Por quantos dias o histórico fica guardado. |
| `WEBHOOK_MAX_ATTEMPTS` | `5` | Tentativas de entregar um aviso. |
| `MAX_MONITORS_PER_USER` | `20` | Limite de monitores por conta. |

## Testes

```bash
pytest --cov
```

O projeto foi feito com TDD, com 100% de cobertura (o CI falha abaixo
disso). Os testes não usam a internet: o DNS e os sites são de mentira.
No CI, a mesma suíte roda no SQLite e no Postgres, as migrações são
aplicadas e desfeitas no Postgres, e o `docker compose` sobe e responde.

## Deploy

O deploy usa o [Render](https://render.com) (API com o worker dentro, pelo
`render.yaml`) e o [Neon](https://neon.tech) (Postgres). No plano grátis
do Render, a API dorme depois de 15 minutos sem visitas, e as checagens
param até alguém abrir a página de novo. Para checar o tempo todo, o
worker pode rodar como processo próprio, como no `docker-compose.yml`.

## Stack

Python 3.13 · FastAPI · SQLAlchemy 2 (async) · Alembic · PostgreSQL ·
asyncpg · httpx/httpcore · PyJWT · argon2 · pytest · ruff · Docker ·
GitHub Actions

## Autor

Arthur Vieira · [LinkedIn](https://www.linkedin.com/in/arthurvieira-dev)
· [GitHub](https://github.com/artvsantos)
