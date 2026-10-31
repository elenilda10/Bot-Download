# Português, inglês e espanhol

Esta revisão migra a interface para catálogos locais em `i18n/locales/pt.json`,
`en.json` e `es.json`. Há 499 chaves com paridade de placeholders e HTML. Nenhuma
API de tradução é chamada durante o atendimento do bot.

## Comportamento

- Português, inglês e espanhol aparecem com seus nomes nativos no seletor.
- A escolha manual é persistida no registro de usuário/chat e prevalece sobre
  sugestões futuras do Telegram. Em grupos, somente administradores podem
  alterar o idioma do grupo. /lang também respeita esse escopo.
- Variantes como `es-MX`, `es_ES` e `pt-BR` são normalizadas. Idiomas não suportados
  e dados ausentes usam inglês. Um grupo novo sem preferência usa inglês.
- O middleware resolve o idioma antes dos bloqueios/avisos, com ContextVar isolada
  por solicitação. O idioma da conversa é a autoridade para chamadas antigas que
  ainda consultam o perfil do remetente. Fora de solicitações, as funções aceitam
  `lang` explicitamente; na ausência, usam inglês.
- Os callbacks de progresso capturam o locale quando criados, preservando-o ao
  serem executados por um worker. Mensagens business e eventos de membros também
  recebem o resolvedor de locale.
- As funções `bm.*` e os protocolos de callbacks/comandos continuam disponíveis.
  Permanecem alguns ramos PT/EN como adaptadores, mas seus textos usam as mesmas
  chaves de catálogo; esses ramos não limitam o espanhol.
- Menus, botões, mensagens, erros, administração, fila/progresso, gráficos e
  descrições dos comandos usam os catálogos. Métodos que tinham textos somente
  em PT ou EN ganharam os outros idiomas.
- Legendas dos autores, títulos de músicas, nomes, URLs e conteúdo enviado por
  usuários não são traduzidos. Somente o rodapé do bot é localizado. Escaping,
  truncamento e links do conteúdo continuam nas funções existentes.
- O status mantém o emoji Premium de download e troca para o foguete no envio.

As chaves extraídas levam o domínio/função de origem e um sufixo estável. Algumas
são compartilhadas entre chamadas com o mesmo texto. Novas traduções devem ser
adicionadas nos três arquivos e verificadas com os testes; não basta criar um
botão novo. Chaves ausentes em um catálogo usam a variante inglesa e geram log.
Não se traduzem nomes de plataformas, códigos internos, parâmetros de API/SQL,
callback_data, nomes dos comandos ou valores de configuração.

## Instalação completa na VPS

Use `scripts/install_i18n_vps.py` desta revisão. Os instaladores pontuais antigos
agora orientam para a instalação completa, pois copiar apenas um handler deixaria
as dependências de catálogo incompletas.

```bash
cd /tmp/reddit-json-pADBLZ
git fetch origin feature/i18n-es
git switch --detach FETCH_HEAD
/root/Bot-Download/venv/bin/python scripts/install_i18n_vps.py --target /root/Bot-Download
```

O dry run copia os arquivos afetados para um diretório temporário, verifica a
integridade do patch e sua aplicação sobre o código existente, compila os módulos
e valida os catálogos. Se o patch direto falhar por contexto, reconstrói a base
verificada e usa uma comparação de três versões para preservar as mudanças locais.
Nenhum arquivo da instalação é alterado nessa etapa.
A base do patch é `fc89407d2b44c826da80722a2b3325edba8ccaa3`; alterações locais
compatíveis fora dos trechos modificados são preservadas. Se as duas versões modificarem o mesmo trecho de formas incompatíveis,
a instalação para antes de qualquer escrita. Use `--diagnose` para mostrar apenas
os blocos conflitantes e permitir uma adaptação baseada no código real. Não force o patch nem substitua a
pasta inteira do bot; os conflitos precisam de uma adaptação para a VPS.

Após um dry run válido:

```bash
/root/Bot-Download/venv/bin/python scripts/install_i18n_vps.py --target /root/Bot-Download --apply &&
systemctl restart bot-download.service &&
systemctl is-active bot-download.service
```

A instalação faz backups em `backups/i18n-*` e restaura os arquivos já escritos se
ocorrer uma falha de escrita. O backup registra também os arquivos criados. O
script não modifica `.env`, cookies, credenciais, banco ou estado do serviço; o
reinício fica explícito no comando. A coluna de idioma já é Text e não exige uma
migração de esquema.

## Validação

Foram executados 270 testes selecionados, incluindo diagnóstico de falhas: mensagens/teclados anteriores, catálogos,
normalização, fallback, HTML/emojis/placeholders, legendas externas intactas,
escopos privado/grupo, permissões/erros de gravação, isolamento e cancelamento,
progresso em background, persistência SQLite e preservação da escolha após refresh
de perfil, rótulos de calendário, instalador e regressões do handler Reddit.
Também foram compilados os 49 arquivos Python alterados/adicionados. Ruff passou
nos novos módulos e testes; o código existente tem avisos anteriores fora deste
escopo. A suíte completa do projeto não foi executada.

A conferência ao vivo no Telegram/VPS ainda é necessária:

1. /lang no privado e /settings no grupo: escolher Español, confirmar o check e
   tentar alterar como membro sem privilégios.
2. Foto, Reel, vídeo com áudio e álbum; observar download, progresso e envio.
3. Reenviar links para testar cache e usar os botões MP3/arquivo e modo inline.
4. Erros, fila/antiflood e inscrição obrigatória no idioma escolhido.
5. /help, /stats, /ping e painel administrativo; conferir textos longos e gráficos.
6. Reiniciar o serviço e verificar que usuários e grupos mantêm a preferência.

A inicialização registra descrições de comandos para pt/en/es; o Telegram pode
apresentar o menu de comandos conforme o idioma do aplicativo, enquanto as
respostas do bot respeitam a preferência escolhida no bot.

A revisão do instalador também foi testada com alterações locais que invalidam
o contexto do patch mas permitem merge sem conflito, com mensagens alteradas dos
dois lados que devem ser recusadas, e com diagnóstico limitado aos blocos em
conflito. O merge nunca escolhe automaticamente um dos lados de um conflito.

### Respostas locais da VPS

A VPS tinha trocas de `answer` para `reply` e de `answer_audio` para
`reply_audio` em Eporner, Pornhub e Universal. O instalador identifica essas
escolhas por função, receptor, ordem das chamadas e igualdade completa dos
argumentos com a versão base ou traduzida. Transporta somente a escolha de
reply para as versões temporárias de merge e mantém as traduções. Mudanças
concorrentes de texto/argumentos e chamadas extras não são resolvidas por essa
regra; continuam sujeitas à recusa por conflito. O teste reproduz essas trocas
nos três handlers e verifica que replies e idempotência sejam preservados.
