# Roteiro de integração MDE 0.3.0 com NX IPTV

Este roteiro é para o primeiro teste controlado entre o NX IPTV e o MDE. Use somente uma fonte de mídia própria ou cuja reprodução esteja autorizada.

## 1. Iniciar o MDE no Windows

Extraia o ZIP do branch `mde-0.3.0`. Abra PowerShell na pasta `mde-v0.3.0` e execute:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
$env:MDE_DATABASE_PATH = "$PWD\mde-v030.sqlite3"
$env:MDE_BOOTSTRAP_USER = "nx-teste"
$env:MDE_BOOTSTRAP_PASSWORD = "troque-esta-senha"
$env:MDE_ADMIN_KEY = "troque-esta-chave-admin"
uvicorn mde_v030.main:app --host 0.0.0.0 --port 8000
```

Confirme no navegador:
- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/docs`

Atenção: `0.0.0.0` permite acesso pela rede local. Não encaminhe a porta do roteador para a internet. Para uso somente no próprio PC, prefira `--host 127.0.0.1`.

## 2. Cadastrar fonte e catálogo

No Swagger, envie o cabeçalho `X-MDE-Admin-Key` com a chave definida em `MDE_ADMIN_KEY`.

1. Execute `POST /v1/admin/sources`.
2. Escolha `delivery_mode=proxy` para ocultar o endereço da origem e entregar HLS pelo MDE, ou `redirect` para o player acessar diretamente a origem.
3. Execute `POST /v1/admin/categories`.
4. Execute `POST /v1/admin/media`, informando o ID da fonte e o caminho relativo da mídia.

A fonte deve ser acessível a partir da máquina que executa o MDE. O caminho da mídia deve ser relativo ao endereço base cadastrado.

## 3. Verificar a API antes do NX

Substitua usuário, senha e endereço pelos valores locais configurados:

- Login: `GET /player_api.php?username=nx-teste&password=...`
- Categorias ao vivo: `GET /player_api.php?username=...&password=...&action=get_live_categories`
- Canais: `GET /player_api.php?username=...&password=...&action=get_live_streams`
- Playlist: `GET /get.php?username=...&password=...&type=m3u_plus&output=ts`

A playlist deve começar com `#EXTM3U` e conter endereços `/play/...` do próprio MDE.

## 4. Configurar o NX IPTV

Se o NX oferecer login Xtream Codes, informe:
- **Servidor/URL:** endereço do PC onde o MDE está rodando, por exemplo `http://192.168.1.20:8000`.
- **Usuário:** o valor de `MDE_BOOTSTRAP_USER`.
- **Senha:** o valor de `MDE_BOOTSTRAP_PASSWORD`.

Se o NX estiver no mesmo PC, use `http://127.0.0.1:8000`. Se estiver em outro aparelho, use o IP local do PC e mantenha os dois na mesma rede Wi-Fi/LAN. Libere a porta 8000 no firewall do Windows apenas para a rede privada.

Se o NX aceitar somente M3U, use a URL `/get.php?username=...&password=...`.

## 5. Critérios de aprovação do primeiro teste

- NX autentica no servidor.
- NX carrega as categorias.
- NX lista os canais cadastrados.
- NX inicia uma mídia HLS autorizada.
- No modo proxy, as playlists e os segmentos passam pelo MDE.
- No modo redirect, o player recebe o redirecionamento para a origem autorizada.

## Limitações conhecidas antes do teste

- A compatibilidade foi testada por testes automatizados da API; ainda não foi validada dentro do aplicativo NX IPTV.
- Séries, temporadas e episódios ainda não têm estrutura completa de catálogo e navegação.
- XMLTV/EPG e painel administrativo visual completo ainda não estão implementados.
- O proxy ainda precisa de fixação do IP validado na conexão para reduzir risco de DNS rebinding; não exponha o serviço publicamente.
- Contagem/limite efetivo de conexões simultâneas ainda não está implementado.
- O campo `output` da playlist não transforma o formato do conteúdo upstream. A extensão é uma indicação ao cliente, não uma transcodificação.

O primeiro teste deve ocorrer na rede local, com conteúdo de teste autorizado e sem expor o servidor à internet.
