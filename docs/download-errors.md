# Erros de download

A resposta comum contém um título e um bloco de código JSON com plataforma,
código, motivo reconhecido, orientação e referência aleatória de oito caracteres.
Quando disponível, inclui a classe e um trecho do erro real do extrator, com até
500 caracteres, após remoção de linhas sensíveis, URLs, caminhos e sequências longas. O evento `download_user_failure` registra a mesma
`error_reference`, o código seguro e a classe da exceção, sem copiar o texto bruto da exceção.
Para localizar uma referência na VPS:

```bash
journalctl -u bot-download.service --since '1 hour ago' --no-pager | rg 'A1B2C3D4'
```

`utils/download_errors.py` classifica códigos HTTP, exceções conhecidas e frases
específicas do extrator. HTTP 403 significa acesso recusado, sem presumir conta
privada, sessão expirada ou restrição geográfica. Causas desconhecidas continuam
explícitas como desconhecidas. O stderr completo e linhas contendo credenciais não entram na resposta.
Uma referência identifica o diagnóstico; ela não é um código retornado pela rede.

Os handlers encaminham a exceção capturada ao tratamento central. Instagram,
Reddit e os subprocessos do extrator universal também preservam o motivo quando
retornam sem mídia. As interfaces desses extratores continuam retornando mídia ou
`None`. O coletor é isolado por atualização e reiniciado por solicitação; sucesso
em uma alternativa descarta falhas anteriores. Os workers de Instagram devolvem
códigos e trechos sanitizados ao contexto assíncrono. A consulta de metadados do
YouTube também transfere seu diagnóstico da thread ao handler antes de retornar
sem mídia.

## Instalação

Esta alteração depende da migração PT/EN/ES. O instalador completo
`scripts/install_i18n_vps.py --target /root/Bot-Download --apply --diagnose` inclui
as mudanças. Seleciona a atualização incremental conforme a presença da migração
de idiomas ou do diagnóstico anterior; prepara e valida a mesclagem antes de alterar o destino e cria
backups. Também preserva as escolhas locais compatíveis de `answer` para `reply` e o
`reply_parameters` observado no payload de aviso do grupo, junto com `parse_mode`.
No YouTube, o instalador reconhece a variante local exata `reply(nothing_found)`
e a substitui pelo diagnóstico central, preservando os outros replies. Mudanças
concorrentes no texto ou nos argumentos continuam sendo recusadas.
Não execute um instalador antigo de status ou um patch parcial por cima disso.

A verificação automatizada simula falhas e transporte, sem acessar contas nem
enviar mensagens. O funcionamento real na VPS requer um novo teste pelo bot após
a instalação e o reinício do serviço.
