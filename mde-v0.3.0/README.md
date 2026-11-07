# MDE 0.3.0 — Media Discovery Engine

A versão 0.2.0 permanece preservada no ZIP da raiz do repositório. A linha 0.3.0 evolui separadamente.

O MDE 0.3.0 reúne catálogo, API no formato Xtream, entrega de mídia, EPG e coleta controlada de playlists M3U de fontes cadastradas. A coleta não rastreia a internet nem procura fontes de terceiros: cadastre somente origens próprias ou autorizadas.

## Recursos incluídos

- Catálogo de canais ao vivo, filmes, séries, temporadas e episódios.
- API `player_api.php` com autenticação, categorias, listagens, informações de VOD/séries e EPG curto.
- Playlist `get.php` em formatos M3U e M3U Plus.
- URLs de reprodução por ID de mídia, sem aceitar uma URL upstream arbitrária na rota do player.
- Política de entrega por fonte: `redirect` ou `proxy`.
- Proxy HTTP/HLS com reescrita de playlists aninhadas, segmentos e atributos `URI=`, Range, HEAD e streaming de arquivos.
- Tokens de recursos HLS aleatórios, persistidos, expirados e vinculados ao usuário.
- Usuários administráveis, expiração de contas e limite de sessões simultâneas por impressão de IP/ User-Agent com lease temporário.
- EPG manual e importação de arquivos XMLTV para canais cadastrados; exportação em `xmltv.php`.
- Painel administrativo web em `/` e `/admin`, além do Swagger em `/docs`.
- Worker separado com fila persistente para importar playlists M3U de fontes cadastradas, restrito ao mesmo host/porta e ao caminho base da fonte.
- Migrações aditivas SQLite para atualizar bancos criados por builds anteriores.
- Testes automatizados no GitHub Actions.

## Requisitos

- Python 3.11 ou superior (CI executado em Python 3.12).
- Windows, Linux ou macOS.
- A máquina do MDE precisa conseguir acessar as fontes autorizadas.
- SQLite é o banco incluído e configurado nesta versão.

## Instalação no Windows

Abra PowerShell na pasta `mde-v0.3.0`:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
$env:MDE_DATABASE_PATH = "$PWD\mde-v030.sqlite3"
$env:MDE_BOOTSTRAP_USER = "nx-teste"
$env:MDE_BOOTSTRAP_PASSWORD = "troque-por-uma-senha-forte"
$env:MDE_ADMIN_KEY = "troque-por-uma-chave-administrativa-longa"
uvicorn mde_v030.main:app --host 127.0.0.1 --port 8000
```

Abra:
- Painel: `http://127.0.0.1:8000/admin`
- Saúde: `http://127.0.0.1:8000/health`
- Swagger: `http://127.0.0.1:8000/docs`

Para um aparelho diferente na rede local, inicie com `--host 0.0.0.0`, descubra o IP local do PC e libere a porta 8000 somente na rede privada no Firewall do Windows. Não faça encaminhamento da porta no roteador.

## Worker de coleta

Mantenha o servidor MDE em execução e abra outro PowerShell na mesma pasta. Ative o mesmo ambiente virtual e configure as mesmas variáveis de ambiente e o mesmo `MDE_DATABASE_PATH`.

```powershell
.venv\Scripts\Activate.ps1
$env:MDE_DATABASE_PATH = "$PWD\mde-v030.sqlite3"
$env:MDE_BOOTSTRAP_USER = "nx-teste"
$env:MDE_BOOTSTRAP_PASSWORD = "troque-por-uma-senha-forte"
$env:MDE_ADMIN_KEY = "troque-por-uma-chave-administrativa-longa"
python -m mde_v030.worker
```

O worker fica consultando a fila. Para processar uma única rodada e sair, use `python -m mde_v030.worker --once`.

### Cadastrar uma fonte para coleta

No painel ou no Swagger, use a chave no cabeçalho `X-MDE-Admin-Key`. Cadastre `base_url` como a raiz permitida e `manifest_path` como o caminho relativo do arquivo M3U. Selecione `catalog_media_type=live` ou `movie`. Depois chame `POST /v1/admin/sources/{source_id}/collect`. A resposta traz `job_id`; consulte `GET /v1/admin/jobs/{job_id}`.

O worker importa nomes, grupos, TVG IDs e caminhos de mídia. Só aceita entradas da mesma origem (mesmo protocolo, host e porta) e dentro do caminho base cadastrado. Itens repetidos são ignorados. Entradas externas ou caminhos fora da fonte são descartados.

## Cadastrar catálogo manualmente

Use o Swagger e envie o cabeçalho `X-MDE-Admin-Key`.

Fonte:
```json
{
  "name": "Minha fonte autorizada",
  "base_url": "https://media.example.net",
  "delivery_mode": "proxy",
  "manifest_path": "catalogo.m3u",
  "catalog_media_type": "live"
}
```

Categoria:
```json
{"name": "Canais de teste", "media_type": "live"}
```

Mídia ao vivo:
```json
{
  "title": "Canal de teste",
  "media_type": "live",
  "source_id": 1,
  "source_path": "channel/master.m3u8",
  "category_id": 1,
  "epg_channel_id": "canal.teste"
}
```

Série:
```json
{
  "title": "Série de teste",
  "media_type": "series",
  "source_id": 1,
  "source_path": "metadata/series",
  "category_id": 2,
  "plot": "Sinopse da série"
}
```

Episódio:
```json
{
  "title": "Episódio piloto",
  "media_type": "episode",
  "source_id": 1,
  "source_path": "series/1/season1/episode1.m3u8",
  "series_id": 2,
  "season_number": 1,
  "episode_number": 1
}
```

Para adicionar uma conta de cliente, use `POST /v1/admin/users`. Senhas exigem ao menos 10 caracteres. `max_connections=0` significa ilimitado; valores positivos limitam as sessões observadas pelo MDE.

## EPG / XMLTV

1. Cadastre um canal ao vivo com `epg_channel_id` ou `tvg_id`.
2. Importe um arquivo XMLTV autorizado enviando seu conteúdo em `POST /v1/admin/epg/import`, no campo `xml_content`, ou registre programas individualmente em `POST /v1/admin/epg/programmes`.
3. O cliente Xtream pode consultar `action=get_short_epg&stream_id=ID`.
4. O guia completo é servido em `/xmltv.php?username=...&password=...`.

A importação XMLTV não aceita DOCTYPE nem declarações de entidades e só grava programas dos IDs de canal já cadastrados.

## API e rotas principais

| Rota | Função |
|---|---|
| `GET /health` | Estado do serviço e do banco |
| `GET /player_api.php` | Login e ações Xtream |
| `GET /get.php` | Playlist M3U |
| `GET /xmltv.php` | Guia XMLTV |
| `GET /play/live/...` | Reprodução de canal |
| `GET /play/movie/...` | Reprodução de filme |
| `GET /series/...` | Reprodução de episódio |
| `GET /play-resource/...` | Segmentos e recursos HLS pelo proxy |
| `GET /admin` | Painel administrativo |
| `POST /v1/admin/sources` | Cadastro de fontes |\n| `PATCH /v1/admin/sources/{id}` | Alterar fontes |\n| `DELETE /v1/admin/sources/{id}` | Remover fonte sem mídias vinculadas |
| `POST /v1/admin/sources/{id}/collect` | Enfileirar coleta |
| `POST /v1/admin/categories` | Cadastro de categorias |
| `POST /v1/admin/media` | Cadastro de mídia |\n| `PATCH /v1/admin/media/{id}` | Alterar mídia |\n| `DELETE /v1/admin/media/{id}` | Remover mídia (séries também removem episódios vinculados) |
| `POST /v1/admin/users` | Cadastro de usuários |\n| `PATCH /v1/admin/users/{username}` | Alterar usuário |\n| `DELETE /v1/admin/users/{username}` | Remover usuário |
| `POST /v1/admin/epg/import` | Importar XMLTV |
| `GET /v1/admin/jobs` | Listagem de coletas |\n| `GET /v1/stats` | Estatísticas administrativas |

## Configurações

| Variável | Padrão | Uso |
|---|---:|---|
| `MDE_DATABASE_PATH` | `mde-v030.sqlite3` | Caminho do banco SQLite |
| `MDE_BOOTSTRAP_USER` | `demo` | Usuário inicial criado somente quando não há usuários |
| `MDE_BOOTSTRAP_PASSWORD` | `change-this-password` | Senha inicial |
| `MDE_ADMIN_KEY` | vazio | Chave obrigatória para rotas administrativas |
| `MDE_PROXY_TOKEN_TTL_SECONDS` | `21600` | Validade dos tokens HLS |
| `MDE_UPSTREAM_TIMEOUT_SECONDS` | `20` | Timeout upstream |
| `MDE_MAX_PLAYLIST_BYTES` | `2097152` | Tamanho máximo de playlist HLS |
| `MDE_PLAYBACK_SESSION_TTL_SECONDS` | `120` | Validade do lease de sessão |
| `MDE_COLLECTION_POLL_SECONDS` | `5` | Intervalo de consulta da fila |
| `MDE_MAX_COLLECTION_BYTES` | `10485760` | Tamanho máximo do catálogo M3U |\n| `MDE_ALLOWED_PRIVATE_HOSTS` | vazio | Hosts privados explicitamente permitidos para laboratório; por exemplo `127.0.0.1:8090,localhost:8090` |

## Testes

```powershell
python -m pytest -v
```

O GitHub Actions executa a suíte no Python 3.12 em cada alteração do branch `mde-0.3.0`.

## Limites conhecidos e segurança

- O MDE usa SQLite nesta versão. PostgreSQL, operação distribuída e implantação de alta disponibilidade ainda não fazem parte deste pacote.
- A proteção de conexões simultâneas usa IP + User-Agent com lease de expiração. É um controle prático para teste local, mas dispositivos atrás do mesmo NAT ou clientes que alterem User-Agent podem fazer a contagem divergir. Não é um sistema de cobrança/DRM.
- A validação de URL bloqueia IPs não públicos, nomes locais, credenciais embutidas, caminhos fora da fonte e redirecionamentos para outros destinos proibidos. A exceção `MDE_ALLOWED_PRIVATE_HOSTS` é opt-in, exige nomes/IPs exatos e deve ser usada somente em laboratório local. Ainda existe risco residual de DNS rebinding entre a resolução e a conexão; a conexão com IP fixado e allowlist de CDN por fonte são requisitos antes de expor o serviço publicamente.
- No modo `redirect`, o player acessa diretamente a origem e vê seu endereço. No modo `proxy`, o endereço da origem não é incluído nas playlists HLS reescritas.
- As URLs Xtream/M3U carregam usuário e senha no caminho por compatibilidade. Não compartilhe playlists e use HTTPS ao publicar o serviço.
- O MDE não transcodifica mídia. A extensão informada ao cliente não converte TS, MP4, MKV ou HLS.
- O catálogo importado não prova que um stream está online ou que é compatível com todos os players.
- Testes automatizados não substituem o teste de reprodução no NX IPTV. A validação no NX ainda está pendente.
- Use somente fontes e conteúdos próprios ou com autorização de reprodução. Não exponha este build de desenvolvimento diretamente à internet.
