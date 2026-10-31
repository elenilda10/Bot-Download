# Instagram sem login

O Instagram usa somente acesso público. Posts, Reels, carrosséis e Stories
continuam passando pelo handler existente e pelas alternativas públicas.
Conteúdos que exigirem uma sessão autenticada podem não estar disponíveis.

Não são lidos cookies do Firefox, arquivos de cookies ou as variáveis
`INSTAGRAM_FIREFOX_PROFILE` e `INSTAGRAM_COOKIES_FILE`. As configurações globais
do gallery-dl e do yt-dlp são ignoradas apenas nas chamadas do Instagram,
para não carregar credenciais configuradas fora do bot.

## Instalação na VPS

Execute o instalador a partir de uma cópia temporária da atualização:

```bash
python scripts/install_instagram_public_vps.py --target /root/Bot-Download --apply
```

Sem `--apply`, apenas simula. Valida a sintaxe, cria backup e altera somente
as fontes de autenticação e os argumentos dos extratores do Instagram.
Preserva as demais personalizações locais. Se encontrar uma estrutura de
login desconhecida, recusa a instalação sem alterar o arquivo.
Não apaga perfis, cookies ou variáveis: outras plataformas podem usá-los.
Após instalar, reinicie `bot-download.service`.

O instalador antigo de cookies redireciona para o instalador de acesso público;
o patch antigo de Stories com Firefox foi desativado.

## Teste local

```bash
python scripts/test_instagram_download.py 'https://www.instagram.com/reel/SHORTCODE/'
```

Esse comando testa os extratores locais sem Telegram, banco ou Cobalt.
