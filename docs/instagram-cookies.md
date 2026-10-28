# Cookies do Instagram

O handler existente usa o serviço compartilhado `fetch_instagram_media`.
A seleção de cookies agora atende posts, Reels, carrosséis e Stories.

No `.env` do bot, configure:

```dotenv
INSTAGRAM_FIREFOX_PROFILE=/home/firefoxbot/.config/mozilla/firefox/p2u2gipq.default-release
```

O perfil precisa conter `cookies.sqlite` legível e sessão válida. Os extratores
leem o perfil em cada execução. Esta mudança não lê sessionstore nem renova
login. Se o perfil estiver ausente ou indisponível, usa o arquivo existente
`/root/Bot-Download/cookies.txt`. `INSTAGRAM_COOKIES_FILE` permite outro caminho;
valor vazio desabilita essa alternativa. Sem fonte disponível, não adiciona
flags de cookies. Perfil legível tem prioridade; falha de autenticação nele
não causa uma segunda tentativa automática com o arquivo.

O gallery-dl recebe `firefox/instagram.com:CAMINHO`; yt-dlp recebe
`firefox:CAMINHO` em metadados e download. Valores dos cookies não são inseridos
nos argumentos nem enviados pelo código às APIs Cobalt. O stderr bruto do
gallery-dl deixa de ser registrado. O helper só adiciona flags em URLs do
Instagram. Configurações próprias dos extratores continuam sendo respeitadas.
O cache e o alcance atual do handler não mudam.

## Testar na VPS

Em checkout temporário de `feature/instagram-firefox-cookies`, use link real:

```bash
/root/Bot-Download/venv/bin/python scripts/test_instagram_download.py \
  'https://www.instagram.com/reel/SHORTCODE/' \
  --firefox-profile /home/firefoxbot/.config/mozilla/firefox/p2u2gipq.default-release
```

O teste executa somente os extratores locais em diretório temporário próprio,
sem Telegram, banco ou Cobalt. Imprime tipos, tamanhos e caminhos, sem cookies.

## Aplicar

```bash
/root/Bot-Download/venv/bin/python scripts/install_instagram_cookies_vps.py \
  --target /root/Bot-Download
```

O padrão é simulação. `--apply` valida a sintaxe, faz backup e altera só a
seleção de cookies em `services/platforms/instagram_media.py`, preservando o
resto do arquivo local, inclusive regras de Stories. Não altera `.env` nem
reinicia o serviço. Depois de instalar e confirmar a configuração, reinicie
`bot-download.service` e teste no Telegram.

Validação: 18 testes isolados de comandos e instalador, Ruff e py_compile.
Download com sessão real e envio no Telegram ainda precisam ser confirmados.

Sintaxe oficial:
- https://gdl-org.github.io/docs/options.html
- https://github.com/yt-dlp/yt-dlp#filesystem-options
