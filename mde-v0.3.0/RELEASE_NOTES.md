# MDE 0.3.0 — Notas de liberação

**Estado:** candidato para primeiro teste de integração local com NX IPTV.  
**Não é uma versão aprovada para exposição pública ou operação de produção.**

## O que está implementado

- API Xtream com autenticação, categorias, canais, filmes, séries, temporadas, episódios e informações de VOD.
- Rotas de reprodução no padrão Xtream: `/live/`, `/movie/` e `/series/`.
- Playlist M3U/M3U Plus usando os caminhos Xtream padrão.
- Entrega por fonte em modo `proxy` ou `redirect`.
- Proxy HLS com reescrita de playlists, segmentos, recursos `URI=`, Range e HEAD.
- Banco SQLite com migrações, usuários, expiração, sessões observadas e limites configurados.
- EPG com importação XMLTV, cadastro de programas, EPG curto Xtream e exportação `xmltv.php`.
- Painel administrativo web para fontes, categorias, usuários, mídias, coletas e EPG.
- API administrativa para criação, edição e exclusão controlada.
- Worker separado com fila persistente para coletar playlists M3U de fontes previamente cadastradas.
- Probe da fonte para verificar a disponibilidade do manifesto e os caminhos que podem ser importados antes de enfileirar a coleta.
- Scripts Windows para instalar, configurar e iniciar servidor e worker.

## Evidência automatizada

A execução mais recente validada no GitHub Actions concluiu:
- Python 3.12: 38 testes aprovados.
- Python 3.14: 38 testes aprovados.

Os testes incluem catálogo, autenticação, séries/episódios, EPG/XMLTV, limites de sessão, painel, worker, idempotência de importação, validação de origem, HLS, Range e modos de entrega. Chamadas externas de mídia nos testes de proxy são simuladas; não representam uma reprodução de um serviço real.

## Restrições conhecidas

- **NX IPTV:** ainda não foi aberto nem testado dentro do aplicativo. Compatibilidade final permanece pendente de integração.
- **Fonte real:** não foi fornecida nem testada nesta etapa. O probe consulta a playlist cadastrada, mas não garante que cada stream reproduza ou tenha codecs compatíveis.
- **DNS rebinding:** a validação bloqueia destinos privados e verifica os redirecionamentos, mas a conexão HTTP ainda não fixa o IP validado. Não exponha este build à internet.
- **Contagem de conexões:** baseada em IP + User-Agent com lease de expiração. É adequada para teste controlado, mas não é identificação confiável de dispositivos atrás de NAT nem um mecanismo de cobrança.
- **Redirect:** o cliente recebe o endereço upstream e a reprodução deixa de passar pelo MDE. Para controle centralizado, prefira proxy.
- **Armazenamento:** esta build usa SQLite. PostgreSQL, HA e escalabilidade horizontal ainda não foram implementados.
- **Coleta:** o worker 0.3 coleta catálogos M3U de fontes registradas; não faz rastreamento genérico da internet, não baixa torrents e não obtém links de fontes de terceiros.
- **Mídia:** o MDE não transcodifica nem converte contêiner/codecs.
- **Transporte público:** HTTPS, gestão operacional de segredos, rate limiting de login/admin, conexão com IP fixado e testes de carga ainda são necessários antes de qualquer exposição pública.

## Próximo marco

Executar o MDE na máquina local, cadastrar uma fonte autorizada, passar o probe, importar o catálogo, cadastrar no NX usando URL/usuário/senha e testar login, categorias, canais, filmes, séries, EPG e reprodução HLS. Corrigir as diferenças concretas encontradas no NX antes de declarar compatibilidade.
