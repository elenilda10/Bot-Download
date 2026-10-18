# Router XVideos — integração controlada

O router `handlers/xvideos.py` é registrado antes do handler universal. Recebe URLs HTTPS válidas de vídeos em `xvideos.com` e usa o endpoint público do próprio player, `/html5player/getvideo/<encoded-id>/<cdn-id>`, para obter fontes `hls`, `mp4_high` e `mp4_low`.

## Funcionamento

- Prefere HLS e seleciona a melhor qualidade disponível até 1080p; se falhar, tenta MP4 de maior qualidade e depois MP4 inferior.
- Mantém o fluxo de envio do bot existente (aiogram + `CUSTOM_API_URL`), respeitando bloqueios, deduplicação, configuração de legendas e limite de tamanho.
- Não usa cookies do Firefox nem a modificação experimental do extrator de XVideos no `yt-dlp` instalado no ambiente de testes.
- Limita downloads simultâneos a 2 e usa pastas temporárias que são apagadas após a tentativa de envio.
- Restringe as URLs fornecidas pelos usuários a páginas de vídeo conhecidas e as fontes retornadas a HTTPS em domínios de CDN do próprio serviço.
- Não desabilita a verificação TLS.

## Observação importante sobre TLS

Durante o diagnóstico em 2026-10-18, um CDN de assets forneceu só o certificado folha, sem o intermediário da Sectigo. A cadeia completa foi validada com OpenSSL e um bundle temporário em `/tmp/sectigo-teste-ca.pem`.

Antes de ativar a integração na VPS, copie esse **bundle já validado** para um caminho persistente e restrito, por exemplo `/root/Bot-Download/certs/xvideos-ca.pem`, e configure em `.env`:

```env
XVIDEOS_CA_BUNDLE=/root/Bot-Download/certs/xvideos-ca.pem
```

O arquivo deve conter a cadeia padrão de autoridades confiáveis e o intermediário validado. Sem essa configuração, use o trust store do sistema; caso algum CDN apresente cadeia incompleta, a conexão pode falhar. Nunca aplique `--no-check-certificates` para contornar o problema.

## Antes de implantar

1. Revisar o PR e a versão atual do código rodando na VPS. O diretório de produção possui alterações locais; não executar `git pull` ou `git reset` diretamente nele.
2. Validar os testes: `python -m unittest discover -s tests -p 'test_xvideos_media.py'`.
3. Testar uma URL conhecida em ambiente isolado, usando o bundle persistente, e validar o vídeo final com `ffprobe`.
4. Conferir que o Bot API personalizado aceita o envio de arquivos grandes e que há espaço em disco para o download.
5. Criar backup e planejar implantação/reversão; **não reiniciar `bot-download.service` nem outros serviços antes da autorização**.

Esta alteração foi preparada numa branch separada e ainda não foi validada no bot de produção.
