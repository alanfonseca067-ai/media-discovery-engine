# MDE 0.3.0 — catálogo, API Xtream e entrega de mídia

A versão 0.2.0 permanece preservada no ZIP da raiz do repositório. A linha 0.3.0 é desenvolvida separadamente.

## Recursos implementados

- Banco SQLite com usuários, categorias, fontes, itens de mídia e tokens temporários de recursos HLS.
- Política de entrega configurável individualmente por fonte: `redirect` ou `proxy`.
- API compatível com o formato básico Xtream em `player_api.php`.
- Geração de playlist M3U em `get.php`.
- Rotas de reprodução autenticadas em `/play/...`.
- Proxy de mídia com encaminhamento de Range e resposta em streaming para segmentos/arquivos.
- Proxy HLS com reescrita de URIs de playlists, incluindo linhas de segmentos e atributos `URI=` usados por chaves, mapas e faixas.
- Tokens aleatórios persistidos no banco e com expiração para recursos internos do proxy HLS.
- Validação de destinos HTTP/HTTPS, rejeição de IPs não públicos e validação de cada redirecionamento HTTP seguido pelo proxy.
- Proteção administrativa por `X-MDE-Admin-Key`, comparada em tempo constante.
- Testes automatizados para autenticação, rotas administrativas, validação de destino e reescrita HLS.

## Instalação no Windows

No PowerShell:

```powershell
cd mde-v0.3.0
py -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev]"
$env:MDE_DATABASE_PATH = "mde-v030.sqlite3"
$env:MDE_BOOTSTRAP_USER = "demo"
$env:MDE_BOOTSTRAP_PASSWORD = "troque-por-uma-senha-forte"
$env:MDE_ADMIN_KEY = "troque-por-uma-chave-administrativa-longa"
uvicorn mde_v030.main:app --host 127.0.0.1 --port 8000
```

Abra `http://127.0.0.1:8000/health` e `http://127.0.0.1:8000/docs`.

Para executar os testes:

```powershell
python -m pytest -v
```

## Cadastrar fonte, categoria e mídia

No Swagger (`/docs`), use `POST /v1/admin/sources` e envie o cabeçalho `X-MDE-Admin-Key`.

Exemplo de fonte em modo proxy:

```json
{"name":"Minha fonte autorizada","base_url":"https://media.example.net","delivery_mode":"proxy","enabled":true}
```

Para usar redirecionamento direto, defina `"delivery_mode":"redirect"`.

Crie uma categoria com `POST /v1/admin/categories`:

```json
{"name":"Canais de teste","media_type":"live"}
```

Cadastre uma mídia com `POST /v1/admin/media`:

```json
{"title":"Canal de teste","media_type":"live","source_id":1,"source_path":"channel/master.m3u8","category_id":1}
```

O campo `source_path` precisa ser relativo ao endereço base da fonte. O servidor não aceita uma URL arbitrária enviada na rota de reprodução: o cliente informa somente o ID da mídia cadastrada.

## Variáveis de ambiente adicionais

| Variável | Padrão | Função |
|---|---:|---|
| `MDE_PROXY_TOKEN_TTL_SECONDS` | `21600` | Validade dos tokens de recursos HLS (6 horas) |
| `MDE_UPSTREAM_TIMEOUT_SECONDS` | `20` | Timeout de conexão/leitura upstream |
| `MDE_MAX_PLAYLIST_BYTES` | `2097152` | Tamanho máximo de playlist HLS (2 MiB) |

## Segurança e limites atuais

- O proxy aceita somente HTTP/HTTPS e bloqueia destinos resolvidos para endereços IP não públicos, nomes locais e redirecionamentos para destinos bloqueados.
- Essa validação reduz SSRF, mas não elimina completamente riscos de DNS rebinding entre a resolução e a conexão. Para exposição pública, a próxima etapa é fixar o IP validado na conexão HTTP e adicionar allowlist de hosts de CDN por fonte.
- O modo `redirect` entrega ao player o endereço upstream. Use-o somente quando essa exposição for aceitável. No modo `proxy`, o endereço upstream não é incluído na playlist reescrita.
- As URLs Xtream/M3U contêm usuário e senha no caminho, conforme o padrão de compatibilidade. Não compartilhe playlists e use HTTPS quando publicar o serviço.
- Ainda não estão implementados limite real de conexões simultâneas, painel administrativo web completo, XMLTV, temporadas/episódios detalhados, integração NX IPTV, nem validação de compatibilidade com players comerciais.
- Uma fonte cadastrada não garante que o conteúdo esteja disponível ou que o formato seja reproduzível. Use apenas mídia própria ou autorizada.
- Não exponha esta versão de desenvolvimento diretamente à internet antes de concluir o endurecimento de DNS, configuração de hosts permitidos, HTTPS, gestão de segredos, limites de conexão e testes com players reais.

## Endpoints

- `GET /health`
- `GET /player_api.php?username=...&password=...`
- `GET /get.php?username=...&password=...`
- `GET|HEAD /play/{media_type}/{username}/{password}/{media_id}.{extension}`
- `GET|HEAD /play-resource/{username}/{password}/{media_id}/{token}`
- `POST /v1/admin/sources`
- `GET /v1/admin/sources`
- `POST /v1/admin/categories`
- `POST /v1/admin/media`
