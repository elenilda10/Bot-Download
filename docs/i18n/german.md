# Alemão e seletor em duas colunas

`de.json` contém as mesmas 499 chaves do catálogo inglês, com textos em alemão,
placeholders, HTML e IDs de emojis preservados. Textos de publicações, títulos,
nomes de artistas e erros originais das ferramentas continuam no idioma da fonte.

A ordem do seletor é `pt`, `en`, `es`, `de`: Português/English, Español/Deutsch,
e Voltar em uma linha separada. O idioma atual mantém a marca de seleção. A
escolha usa o escopo existente de usuário/chat e é persistida no banco sem mudança
de esquema. Variantes como `de-DE` e `de_AT` são normalizadas para `de`. O menu de
comandos também possui descrições em alemão para o Telegram.

O agrupamento em pares usa `SUPPORTED_LOCALES`, sem um número fixo de linhas.
Para um próximo idioma, adicione o catálogo completo e registre seu código e nome
nativo em `i18n/__init__.py`. O cache de catálogos e a validação do instalador não
possuem uma lista separada limitada aos três idiomas anteriores. Empacote também
os novos arquivos nos manifestos/patches e execute os testes de paridade.

## Instalação

Use o checkout desta revisão e execute:

```bash
/root/Bot-Download/venv/bin/python scripts/install_german_vps.py \
  --target /root/Bot-Download --apply --diagnose
```

O instalador prepara e valida os arquivos antes de alterar o destino, cria backups
e preserva personalizações locais compatíveis. Não modifica `.env`, cookies,
credenciais, banco nem o estado do serviço. Reinicie o serviço depois da instalação.

A validação inclui a paridade de todas as chaves e seus campos/HTML, botões com
quatro idiomas e expansão para cinco, escolha de alemão no grupo, persistência
SQLite após atualização do perfil, descrições de comandos e avisos de download.
O teste da instalação sobre o runtime publicado verifica preservação de alterações
locais, idempotência e recusa de um catálogo com placeholders incorretos.
