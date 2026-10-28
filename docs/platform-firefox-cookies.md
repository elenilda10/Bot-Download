# Perfil do Firefox nos extratores locais

## Plataformas revisadas

| Plataforma | Integração |
| --- | --- |
| Instagram | Perfil em gallery-dl e yt-dlp para posts, Reels, carrosséis e Stories; instalador próprio |
| Twitter/X | Perfil no fallback yt-dlp local |
| TikTok | Perfil nas opções yt-dlp de metadados e download |
| Reddit | Perfil no gallery-dl local |
| YouTube | Perfil em metadados, vídeos e áudio que usam o builder compartilhado |
| Universal | Perfil apenas para URLs reconhecidas das quatro plataformas acima, em gallery-dl/yt-dlp |
| Facebook | Integração própria existente, preservada |
| Kwai | Integração própria de cookies persistentes e de sessão, preservada |
| Pinterest e outros serviços | Caminhos públicos/API atuais preservados |

A configuração é opcional. O suporte não significa que toda publicação exige
login nem garante acesso quando a sessão expirou ou a plataforma bloqueia o
extrator. Valores de cookies não são passados manualmente a APIs Cobalt ou
serviços alternativos. Cada extrator local carrega o perfil e seu cookie jar
aplica as regras de domínio. O leitor não faz login nem renova cookies.

## Configuração

Um perfil compartilhado para Twitter/X, TikTok, Reddit e YouTube:

```dotenv
MEDIA_FIREFOX_PROFILE=/home/firefoxbot/.config/mozilla/firefox/p2u2gipq.default-release
```

Também são aceitos `TWITTER_FIREFOX_PROFILE`, `TIKTOK_FIREFOX_PROFILE`,
`REDDIT_FIREFOX_PROFILE` e `YOUTUBE_FIREFOX_PROFILE`. Um valor por plataforma
substitui o compartilhado; um valor explicitamente vazio desabilita o perfil
compartilhado nessa plataforma. Sem perfil legível, os arquivos antigos de
cookies continuam sendo usados. Ao selecionar o perfil, a nova integração não
adiciona simultaneamente o arquivo de cookies.

No YouTube, a configuração já existente
`YTDLP_YOUTUBE_COOKIES_FROM_BROWSER` tem prioridade e conserva seu comportamento.
Instagram, Facebook e Kwai continuam usando suas variáveis próprias. O perfil
compartilhado não ativa a sessão do Facebook no Universal nem habilita cookies
do navegador para domínios desconhecidos.

## Instalar na VPS

Em checkout da branch `feature/instagram-firefox-cookies`:

```bash
/root/Bot-Download/venv/bin/python scripts/install_platform_cookies_vps.py \
  --target /root/Bot-Download \
  --firefox-profile /home/firefoxbot/.config/mozilla/firefox/p2u2gipq.default-release
```

Sem `--apply` é apenas simulação. O instalador prepara o patch em pasta
isolada, valida sintaxe e preserva alterações locais fora dos trechos do patch.
Se os trechos locais divergirem, recusa a instalação sem alterar o destino.
Com `--apply`, faz backup, instala seis integrações e o helper compartilhado.
`--firefox-profile` configura somente `MEDIA_FIREFOX_PROFILE` no `.env`, também
com backup; sem a opção, o ambiente não muda. Não reinicia o serviço.
O instalador do Instagram continua separado e não precisa ser repetido se já
foi aplicado. Reinicie `bot-download.service` depois de aplicar.

Testes isolados cobrem precedência/desativação, domínios, builders reais do
TikTok/YouTube e instalação com preservação do arquivo local e `.env`. A sessão
real deve ser conferida na VPS com links de cada plataforma. Os testes não
usam cookies reais nem fazem chamadas de download.
