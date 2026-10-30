# Erros de download

A resposta comum contém plataforma, motivo reconhecido, orientação e uma referência
aleatória de oito caracteres. O evento `download_user_failure` registra a mesma
`error_reference`, o código seguro e a classe da exceção, sem copiar seu texto.
Para localizar uma referência na VPS:

```bash
journalctl -u bot-download.service --since '1 hour ago' --no-pager | rg 'A1B2C3D4'
```

`utils/download_errors.py` classifica códigos HTTP, exceções conhecidas e frases
específicas do extrator. HTTP 403 significa acesso recusado, sem presumir conta
privada, sessão expirada ou restrição geográfica. Causas desconhecidas continuam
explícitas como desconhecidas. Nenhum stderr, cookie ou token entra na resposta.
Uma referência identifica o diagnóstico; ela não é um código retornado pela rede.

Os handlers encaminham a exceção capturada ao tratamento central. Instagram,
Reddit e os subprocessos do extrator universal também preservam o motivo quando
retornam sem mídia. As interfaces desses extratores continuam retornando mídia ou
`None`. O coletor é isolado por atualização e reiniciado por solicitação; sucesso
em uma alternativa descarta falhas anteriores. Os workers de Instagram devolvem
apenas códigos seguros ao contexto assíncrono.

## Instalação

Esta alteração depende da migração PT/EN/ES. O instalador completo
`scripts/install_i18n_vps.py --target /root/Bot-Download --apply --diagnose` inclui
ambas as mudanças. Seleciona a atualização incremental para uma versão que já
tenha `i18n/__init__.py`; prepara e valida a mesclagem antes de alterar o destino e cria
backups. Também preserva as escolhas locais compatíveis de `answer` para `reply`.
Não execute um instalador antigo de status ou um patch parcial por cima disso.

A verificação automatizada simula falhas e transporte, sem acessar contas nem
enviar mensagens. O funcionamento real na VPS requer um novo teste pelo bot após
a instalação e o reinício do serviço.
