# Varredura para ampliar os idiomas do Bot-Download

Este documento registra a base anterior à migração. A implementação e instalação
estão descritas em [migration.md](migration.md).

## Escopo e resultado

Base examinada: `fc89407d2b44c826da80722a2b3325edba8ccaa3`, branch
`feature/platform-download-status`, incluindo as alterações recentes de Reddit,
cookies Firefox e mensagens de transferência. A branch `main` ainda estava em
`41dc3fb12dae9f9ba029cdbcfd397608152bfe9d` na consulta. Esta auditoria não afirma
que todos esses arquivos já estejam instalados na VPS.

Foram analisados **155 arquivos Python**, sem erros de sintaxe. A análise AST
inventariou **8.966 ocorrências de strings/templates** do código: **950 candidatas
à revisão de interface, em 67 arquivos**, 752 ocorrências de diagnóstico e 7.264
ocorrências classificadas como técnicas/dados. Comentários, docstrings, testes e
scripts de manutenção ficam fora do inventário de execução. Não há arquivos
HTML/JS de interface separados no repositório; os textos e HTML embutidos estão
nos módulos Python. YAML de CI/infraestrutura não integra a interface do bot.

| Grupo de candidatos | Ocorrências |
| --- | ---: |
| Textos em módulos centrais de mensagens | 353 |
| Candidatos em teclados | 137 |
| Literais candidatos dentro de chamadas de interface | 110 |
| Outras frases a revisar, incluindo auxiliares e dados | 350 |

São **ocorrências no código, não 950 mensagens únicas nem 950 chaves a traduzir**.
Há variantes PT/EN, fragmentos, marcas e falsos positivos. Por exemplo, parâmetros
como `captions` podem ser classificados em chamadas de interface sem serem texto
visível. O inventário preserva arquivo, linha, função, categoria e template para
revisão. Os módulos centrais têm **94 funções públicas em user_messages.py e 23
em admin_messages.py**; nem todas correspondem a uma mensagem única.

Arquivos:

- `docs/i18n/text-inventory.json`: os 950 candidatos e os pontos de roteamento.
- `scripts/audit_i18n.py`: gera o inventário completo de strings, inclusive as
  técnicas/diagnóstico, sem importar o bot nem exigir credenciais.

## Pontos que precisam mudar antes de liberar mais idiomas

1. **Normalização duplicada e binária.** Há 11 funções chamadas `_normalize_lang`
   ou `_is_pt`, em mensagens, teclados, guest, YouTube, Universal, middlewares,
   settings e gráficos. A maioria trata tudo que não começa com `pt` como `en`;
   `services/stats/chart.py:36` faz o inverso: apenas `en` vira inglês e o restante
   vira português. Foram localizadas 183 expressões explícitas de idioma com
   `pt`/`en`; isso não inclui todas as condições indiretas por `is_pt`.
2. **Idioma escolhido pode ser reduzido a PT/EN.** O caminho registrado em
   `handlers/user.py:72` usa `handle_language_selection`; em
   `handlers/settings_menu.py:424` essa função escolhe PT se o callback termina
   em `pt`, caso contrário EN. `set_language_callback` também reduz a dois
   idiomas. Adicionar um botão `es` sozinho ainda selecionaria inglês.
3. **Menu duplicado.** `handlers/settings_menu.py` define `open_language_menu`
   duas vezes (linhas 145 e 356); em Python a última definição substitui a
   anterior para a resolução do nome global. É preciso consolidar funções e
   verificar os registros de callbacks, incluindo privado e grupos.
4. **Persistência não é o único problema.**
   `services/storage/user_repository.py:224` já devolve o idioma salvo e
   `set_language` grava uma string; `middlewares/chat_tracker.py:14` e a resolução
   de `middlewares/channel_subscription.py:39` ainda impõem PT/EN. A escolha
   manual deve prevalecer sobre o idioma sugerido pelo Telegram. O fallback
   também varia: mensagens/teclados usam PT, `services/settings.py` usa EN.
5. **Textos fora dos catálogos.** Há textos visíveis em handlers e middlewares,
   mesmo onde outros textos da mesma função já usam `bm`. Precisam entrar no
   catálogo antes de considerar um idioma completo.
6. **Comandos do Telegram só em inglês.** `config.py:234` contém BOT_COMMANDS com
   descrições fixas; `main.py:375` registra apenas uma lista, sem `language_code`.
   Traduzir os comandos exige registrar descrições por idioma, mantendo `/start`,
   `/settings`, callbacks e identificadores técnicos estáveis.

## Áreas da migração

| Área | Arquivos principais | Conteúdo a cobrir |
| --- | --- | --- |
| Mensagens gerais | messages/user_messages.py | /start, /help, configurações, erros, lotes, encaminhamento, indicação, status e inline |
| Administração | messages/admin_messages.py; handlers/admin.py; handlers/commands.py | bloqueios, limpeza, armazenamento, avisos, envio a chats, métricas |
| Teclados e seleção | keyboards/inline_keyboards.py; handlers/settings_menu.py | botões, categorias, seleção ativa, nomes nativos dos idiomas, confirmações |
| Música | handlers/music.py; handlers/*inline.py | busca, artista/faixa, resultados, erros, MP3 e arquivo |
| Plataformas | handlers/facebook.py; handlers/kwai.py; handlers/universal.py; demais handlers | callbacks, links expirados, conversão, limites, arquivo/foto/vídeo/álbum |
| Download e envio | handlers/telegram_ui_utils.py; services/media/orchestration.py; services/media/delivery.py; services/inline/send_flow.py | fila, rate limit, progresso, envio, tentativas, cache e retomada |
| Restrições e grupos | middlewares/*.py; handlers/guest.py; handlers/channel_membership.py | antiflood, banimento, canal obrigatório, /start privado, mensagens de grupos |
| Configurações e idioma salvo | services/settings.py; services/storage/user_repository.py; handlers/commands.py | rótulos, qualidade/formato, preferência por usuário/chat, idioma inicial |
| Estatísticas | services/stats/chart.py; handlers/admin.py | título, período, meses/dias, eixos e unidades; cache já inclui idioma |
| Menu do Telegram | config.py; main.py | descrições dos comandos por idioma |

### Exemplos confirmados fora dos catálogos

- `handlers/telegram_ui_utils.py:577` e `:585`: rate limit e fila somente em inglês.
- `handlers/facebook.py:284`: botão expirado/conversa diferente somente em PT;
  `:523`: falha ao abrir link com orientação ao usuário somente em PT.
- `handlers/google_drive.py:149`, `handlers/mediafire.py:148`,
  `handlers/mega.py:164`: erro de download/envio somente em PT.
- `handlers/xvideos_actions.py:141`: link expirado somente em PT.
- `services/inline/send_flow.py:373`: legenda técnica “Album Preview” em inglês.
- `handlers/settings_menu.py:264` e `:295`: erro ao salvar duplicado PT/EN no handler.

## Proposta para uma migração conjunta

- Criar um resolvedor único de locale com lista explícita de idiomas suportados,
  normalização de variantes como `pt-BR`/`es-ES` e fallback consistente.
- Criar catálogos por idioma e chaves semânticas; manter as funções `bm.*` como
  adaptadores durante a migração para não quebrar handlers e instalações locais.
- Migrar mensagens, teclados, middlewares, gráficos, descrições de comandos e
  callbacks no mesmo trabalho, respeitando idioma por usuário ou por grupo.
- Usar placeholders nomeados e formatação/plurais para quantidades, fila e lotes;
  preservar HTML, links, emojis Premium e escaping de dados externos.
- Manter nomes de plataforma, URLs, comandos, callback_data, extensões/formatos,
  parâmetros API/SQL e códigos do banco estáveis. Legendas/textos dos autores
  continuam no idioma de origem; o comando /trad é uma funcionalidade separada.
- Diagnósticos e exceções internas não precisam virar traduções de interface.
  Onde o erro bruto é mostrado ao usuário, usar uma mensagem localizada e guardar
  os detalhes no log.
- Traduções ausentes devem cair no idioma de fallback e ser detectadas pela
  validação; só exibir um idioma novo no menu após completar seu catálogo.

## Validação necessária na implementação

1. Paridade das chaves e placeholders entre os idiomas, HTML e IDs de emoji.
2. Nenhuma conversão PT/EN remanescente no roteamento; preferências preservadas
   após nova mensagem, callback, reinício e uso em grupos.
3. Mensagens de sucesso/erro, download/upload, cache, inline, MP3/documento,
   álbuns, fila, banimento e inscrição obrigatória no idioma selecionado.
4. Comandos, botões e gráficos no idioma correto, incluindo textos compridos.
5. Reexecução do scanner, revisão manual dos candidatos e teste real no Telegram.

## Como reproduzir

```bash
python scripts/audit_i18n.py --json /tmp/bot-i18n-full.json
```

A análise é estática: identifica strings e locais de seleção, mas não executa os
fluxos e não prova que cada ramo seja alcançável ou que todo texto vindo de dados
externos esteja coberto. O relatório usa linhas da revisão examinada; elas podem
mudar após migração. Nenhuma tradução, comportamento do bot, configuração ou
serviço da VPS foi alterado nesta etapa.
