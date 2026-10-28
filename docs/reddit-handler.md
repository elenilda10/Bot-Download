# Handler dedicado do Reddit

`handlers/reddit.py` é registrado antes do Universal. Atende texto/legenda,
privado, grupos, supergrupos e business. A detecção compartilhada e o despacho
de pedidos pendentes/lotes agora reconhecem o serviço `reddit`.

Aceita posts `/comments/ID`, galerias `/gallery/ID`, links curtos `redd.it/ID`,
compartilhamentos `/r/sub/s/TOKEN` e mídias `i.redd.it`/`v.redd.it`. Não baixa
subreddits ou perfis inteiros. Domínios parecidos são rejeitados. Parâmetros
assinados de `preview.redd.it` são preservados.

O adapter `services/platforms/reddit_download.py` reutiliza o downloader local
compartilhado gallery-dl/yt-dlp e a seleção de cookies já instalada. Não encaminha
a requisição ao handler Universal. Valida os arquivos locais e o limite de bytes
antes de enviar. Mantém o caminho de extração anterior; não substitui o backend
por um download direto de URLs de vídeo potencialmente sem áudio.

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
instalar os três módulos novos e adaptar registro, detecção, despacho de lotes
e nome de exibição. Preserva o restante desses arquivos locais e recusa anchors
incompatíveis. Requer o helper de cookies instalado anteriormente; não altera
Kwai, Instagram ou outros handlers e não reinicia o serviço.

Validação local usa mídia, Telegram e downloader simulados. Ainda é necessário
confirmar extração com sessão real, vídeo com áudio e envio Telegram na VPS.
Não adiciona um fluxo dedicado de inline query nesta etapa.
