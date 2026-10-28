# Handler dedicado do Reddit

`handlers/reddit.py` é registrado antes do Universal. Atende texto/legenda,
privado, grupos, supergrupos e business. A detecção compartilhada e o despacho
de pedidos pendentes/lotes agora reconhecem o serviço `reddit`.

Aceita posts `/comments/ID`, galerias `/gallery/ID`, links curtos `redd.it/ID`,
compartilhamentos `/r/sub/s/TOKEN` e mídias `i.redd.it`/`v.redd.it`. Não baixa
subreddits ou perfis inteiros. Domínios parecidos são rejeitados. Parâmetros
assinados de `preview.redd.it` são preservados.

O adapter `services/platforms/reddit_download.py` consulta primeiro o JSON da
publicação com `curl_cffi`/Firefox147, usando apenas os cookies persistentes do
perfil configurado. O ID retornado precisa corresponder ao solicitado. Os
links `/s/` são resolvidos com redirecionamentos limitados ao Reddit.

Para galerias estáticas JPEG/PNG, usa somente os IDs declarados em
`gallery_data.items` e seus registros em `media_metadata`, preservando a ordem.
Baixa até três imagens simultaneamente e valida os arquivos com Pillow. Há
limite de bytes, dimensões e tempo; sessões separadas sem cookies autenticados
baixam os arquivos no CDN. Não encaminha a requisição ao handler Universal.

Tipos não atendidos pelo caminho de fotos conservam o downloader compartilhado
gallery-dl/yt-dlp, incluindo o caminho de vídeo com merge de áudio. Rejeições ou
falhas no JSON encerram a tentativa, sem iniciar os fallbacks longos. Essa
política não garante acesso quando o Reddit bloqueia a requisição. O caminho
rápido usa somente cookies persistentes: o teste manual na VPS retornou HTTP
200 e as três imagens com e sem o complemento de sessão.

## Entrega

- Foto/vídeo únicos ou álbum misto em grupos de até dez mídias.
- Modo documento, legenda no primeiro item, resposta à mensagem original e
  tolerância à ausência do link quando a API Telegram a permite.
- Cache próprio de manifests/file_ids, separado de Universal e por modo.
  Com Firefox configurado, o cache é separado por usuário e conversa.
- Botões MP3 e arquivo para mídia única, com recuperação da URL pelo botão de
  origem após reinício. Álbuns não têm botões de ação nesta versão.
- Dedupe, bloqueios existentes, settings do usuário/grupo e limpeza do diretório
  temporário por pedido, inclusive em falhas. Após envio parcial de álbum não
  reinicia o álbum automaticamente.

O handler usa as variáveis `REDDIT_FIREFOX_PROFILE` ou `MEDIA_FIREFOX_PROFILE`
introduzidas no suporte anterior. Não altera o `.env` na instalação. As opções
de cookies por arquivo do downloader compartilhado continuam funcionando.

## Teste na VPS

Em checkout da branch `feature/reddit-handler`:

```bash
/root/Bot-Download/venv/bin/python scripts/test_reddit_download.py \
  'https://www.reddit.com/r/SUB/comments/POST_ID/' \
  --firefox-profile /home/firefoxbot/.config/mozilla/firefox/p2u2gipq.default-release
```

Use um link real. O teste usa somente o downloader local, sem Telegram, banco,
login nem exportação de cookies. Imprime os arquivos para inspeção, sem cookies.

## Instalação seletiva

```bash
/root/Bot-Download/venv/bin/python scripts/install_reddit_handler_vps.py \
  --target /root/Bot-Download
```

Sem `--apply` é simulação. Com `--apply`, valida a sintaxe e faz backup antes de
instalar os quatro módulos dedicados e adaptar registro, detecção, despacho de lotes
e nome de exibição. Preserva o restante desses arquivos locais e recusa anchors
incompatíveis. Requer o helper de cookies instalado anteriormente; não altera
Kwai, Instagram ou outros handlers e não reinicia o serviço.

Validação local usa mídia, Telegram e downloader simulados. Ainda é necessário
confirmar extração com sessão real, vídeo com áudio e envio Telegram na VPS.
Não adiciona um fluxo dedicado de inline query nesta etapa.


## Medição e validação da atualização de galerias

O smoke test imprime o tempo total. Os logs distinguem a consulta JSON e o
resultado da galeria. O status no Telegram usa “Baixando mídia…” e “Enviando
mídia…”. O caminho de fotos tem consulta com orçamento de 20 segundos e
conjunto de downloads com orçamento de 60 segundos, até 20 por imagem. Isso
não significa que esse tempo será consumido em pedidos normais.

Testes simulados cobrem o transporte Firefox, resolução de links curtos,
recusa de redirects externos, validação do ID, ordem e concorrência das fotos,
rejeição de mídia externa/inválida/grande e limpeza de galerias parciais. O
HTTP 200 com o perfil foi confirmado manualmente pelo usuário na VPS; o novo
download completo de fotos ainda precisa ser confirmado lá. Em ambiente local
sem sessão, o mesmo post respondeu 403 e a tentativa encerrou em 4,74 s,
sem passar pelos fallbacks; isso não mede a velocidade de download na VPS.
