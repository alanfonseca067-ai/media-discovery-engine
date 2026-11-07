# Preparação do MDE 0.3.0 para integração com NX IPTV

Este roteiro instala o servidor, o painel e o worker e valida a API antes de cadastrar o MDE no NX. Use somente fontes e mídias próprias ou autorizadas.

## 1. Instalar no Windows

Extraia o ZIP do branch `mde-0.3.0`. Abra PowerShell na pasta `mde-v0.3.0`:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
$env:MDE_DATABASE_PATH = "$PWD\mde-v030.sqlite3"
$env:MDE_BOOTSTRAP_USER = "nx-teste"
$env:MDE_BOOTSTRAP_PASSWORD = "troque-por-uma-senha-forte"
$env:MDE_ADMIN_KEY = "troque-por-uma-chave-administrativa-longa"
# Descomente somente se a origem de mídia do laboratório estiver no próprio PC:
# $env:MDE_ALLOWED_PRIVATE_HOSTS = "127.0.0.1,localhost"
uvicorn mde_v030.main:app --host 127.0.0.1 --port 8000
```

Abra:
- Painel: `http://127.0.0.1:8000/admin`
- Saúde: `http://127.0.0.1:8000/health`
- API/Swagger: `http://127.0.0.1:8000/docs`

Deixe esse terminal aberto.

## 2. Iniciar o worker de coleta

Abra outro PowerShell na mesma pasta e configure o mesmo ambiente e banco:

```powershell
.venv\Scripts\Activate.ps1
$env:MDE_DATABASE_PATH = "$PWD\mde-v030.sqlite3"
$env:MDE_BOOTSTRAP_USER = "nx-teste"
$env:MDE_BOOTSTRAP_PASSWORD = "troque-por-uma-senha-forte"
$env:MDE_ADMIN_KEY = "troque-por-uma-chave-administrativa-longa"
# Use a mesma allowlist do servidor se a playlist do laboratório for local:
# $env:MDE_ALLOWED_PRIVATE_HOSTS = "127.0.0.1,localhost"
python -m mde_v030.worker
```

Deixe esse terminal aberto também. O worker processa os jobs enfileirados. Para processar somente uma vez e sair, use `python -m mde_v030.worker --once`.

## 3. Cadastrar e coletar um catálogo autorizado

No painel, informe a chave de `MDE_ADMIN_KEY`. Cadastre uma fonte com:
- `base_url`: origem raiz autorizada.
- `manifest_path`: caminho relativo da playlist M3U.
- `catalog_media_type`: `live` para canais ou `movie` para filmes.
- `delivery_mode`: `proxy` ou `redirect`.

Clique em enfileirar coleta, ou no Swagger execute `POST /v1/admin/sources/{source_id}/collect`. Consulte o resultado em `GET /v1/admin/jobs/{job_id}` ou no painel.

O worker só importa entradas do mesmo protocolo, host e porta e dentro do caminho base da fonte. Não aceita URLs de terceiros em uma playlist da fonte. Duplicatas são ignoradas.

## 4. Validar a API antes do NX

Use o usuário e a senha de bootstrap definidos acima:

- Login/info: `GET /player_api.php?username=nx-teste&password=...`
- Categorias: `GET /player_api.php?username=...&password=...&action=get_live_categories`
- Canais: `GET /player_api.php?username=...&password=...&action=get_live_streams`
- Filmes: `GET /player_api.php?username=...&password=...&action=get_vod_streams`
- Séries: `GET /player_api.php?username=...&password=...&action=get_series`
- Playlist: `GET /get.php?username=...&password=...&type=m3u_plus&output=ts`
- XMLTV: `GET /xmltv.php?username=...&password=...`

Para testar temporadas e episódios, cadastre uma mídia do tipo `series` e depois mídias do tipo `episode` vinculadas a ela com `series_id`, `season_number` e `episode_number`. Consulte `action=get_series_info&series_id=ID`.

Para testar EPG, cadastre um canal com `epg_channel_id` e importe XMLTV pelo painel/API em `POST /v1/admin/epg/import`. O endpoint XMLTV só exporta canais já cadastrados.

## 5. Configurar o NX IPTV

Se o NX oferece login Xtream Codes:

- **Servidor:** `http://127.0.0.1:8000` quando o NX roda no mesmo PC.
- **Usuário:** `nx-teste` (ou o valor de `MDE_BOOTSTRAP_USER`).
- **Senha:** a senha configurada em `MDE_BOOTSTRAP_PASSWORD`.

Se o NX estiver em outro aparelho na mesma rede:
1. Pare o Uvicorn com CTRL+C.
2. Reinicie com `--host 0.0.0.0 --port 8000`.
3. Descubra o IPv4 local do PC com `ipconfig`.
4. Use no NX `http://IP-DO-PC:8000`.
5. Libere a porta 8000 apenas para a rede privada no Firewall do Windows.

Não encaminhe essa porta no roteador e não exponha o serviço à internet.

Se o NX aceitar somente M3U, use `http://IP-DO-PC:8000/get.php?username=...&password=...`.

## 6. Critérios de aprovação da integração

- Login Xtream aceito pelo NX.
- Categorias e canais carregados.
- Filmes e séries listados.
- Detalhes das séries mostram temporadas e episódios.
- EPG aparece para canais que têm programação cadastrada.
- Ao abrir um canal HLS, o NX recebe a playlist e os segmentos.
- O modo proxy mantém playlist/segmentos no endereço do MDE.
- O modo redirect redireciona diretamente para a fonte cadastrada.

## Limites que ainda exigem validação real

- Ainda não foi executado teste dentro do aplicativo NX IPTV nesta etapa.
- Por padrão, a validação bloqueia destinos privados e verifica redirects. Para laboratório local, uma allowlist explícita pode liberar somente os hosts privados informados. A conexão ainda não fixa o IP resolvido; DNS rebinding residual impede recomendar exposição pública.
- O limite de conexões é um lease baseado em IP + User-Agent e expira após inatividade. Clientes atrás do mesmo NAT ou que alterem User-Agent podem afetar a contagem.
- SQLite está pronto para instalação local; PostgreSQL, alta disponibilidade e escalabilidade horizontal permanecem fora desta build.
- O MDE entrega os formatos originais, não faz transcodificação. A extensão de URL não converte o conteúdo.
- Compatibilidade final de cada player depende de suas particularidades Xtream/HLS, codecs e comportamento de autenticação.

O teste inicial deve ser feito na rede local, com um catálogo de teste autorizado.