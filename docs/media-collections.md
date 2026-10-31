# Playlists e álbuns

- YouTube: links `/playlist?list=…` e links de vídeo que incluam `list=` são
  processados como coleções. IDs de álbum `OLAK…` preferem MP3 mesmo no domínio
  `youtube.com`; as demais playlists usam vídeo. As preferências de qualidade/documento
  existentes continuam valendo por item.
- YouTube Music: playlists e álbuns compartilhados como playlists são enviados
  como áudios MP3.
- Spotify: `/album/…` e `/playlist/…`, incluindo o prefixo `intl-…`, usam o fluxo
  atual de busca de uma fonte no YouTube e envio de MP3 com metadados Spotify.
  A nova função não extrai áudio protegido diretamente do Spotify.

Cada item é enviado separadamente, em ordem. Itens repetidos são mantidos. O
status mostra a posição atual e o resumo informa enviados, falhas, indisponíveis
e restantes. Uma falha de item não encerra toda a coleção. O botão Cancelar só
pode ser usado por quem fez o pedido e termina após o item atual. Limites de
fila/requisições ou falhas do Telegram interrompem a coleção para evitar várias
solicitações que já seriam recusadas. Cancelamentos e falhas não marcam a coleção
como concluída para deduplicação.

`COLLECTION_MAX_ITEMS` define o limite por solicitação (padrão 50, máximo 200).
`COLLECTION_MAX_ACTIVE` limita coleções simultâneas no processo (padrão 3, máximo
10), e cada usuário pode ter apenas uma em andamento. O processamento também
para após uma hora, ao concluir o item atual. Esses limites são avisados na
interface; os itens não processados não são apresentados como enviados.

A enumeração Spotify tenta a API com as credenciais já configuradas, com
paginação reconstruída no host oficial. O campo `next` nunca é seguido com um
token para um host arbitrário. Sem acesso à API, tenta o embed público e verifica
a identidade da coleção. Como o embed pode omitir faixas, esse resultado é sempre
identificado como parcial. Coleções privadas ou que não exponham metadados
acessíveis ao bot podem falhar. O cache individual de arquivos é reaproveitado;
arquivos temporários de áudio de coleções usam nomes exclusivos por tentativa.

## VPS

Execute `scripts/install_collections_vps.py --target /root/Bot-Download --apply
--diagnose` a partir deste checkout. A instalação usa a mesclagem em staging e
backups já existentes, preservando alterações locais compatíveis de reply. Não
altera `.env`, cookies, banco de dados nem reinicia o serviço automaticamente.

Teste de enumeração, sem baixar arquivos nem enviar mensagens:

```bash
/root/Bot-Download/venv/bin/python scripts/test_media_collection.py \
  'URL_DA_PLAYLIST_OU_ALBUM' --limit 3 \
  --firefox-profile /home/firefoxbot/.config/mozilla/firefox/p2u2gipq.default-release
```

A validação automatizada usa metadados e entregas simuladas. O teste real de
cookies, disponibilidade da API Spotify, correspondência das faixas na busca e
entrega no Telegram deve ser feito na VPS.

Referências: [yt-dlp](https://github.com/yt-dlp/yt-dlp),
[Spotify Playlist Items](https://developer.spotify.com/documentation/web-api/reference/get-playlists-items)
e [Spotify Album Tracks](https://developer.spotify.com/documentation/web-api/reference/get-an-albums-tracks).
