# Moderação NSFW local para grupos

A moderação usa a biblioteca NudeNet diretamente na VPS, sem hospedagem API externa, sem webhook separado e sem depender do Telebot Creator. O fluxo de download do Save Vid permanece independente.

## Dependências

Dentro do ambiente Python já usado pelo Bot-Download:

```bash
cd /root/Bot-Download
./venv/bin/python -m pip install -r requirements-nsfw.txt
./venv/bin/python -m scripts.nsfw_scan /caminho/para/foto.jpg --sensitivity balanced
```

O comando retorna JSON com as detecções e `nsfw: true/false`. A CLI é um script de teste; o bot usa a mesma biblioteca Python internamente para evitar iniciar um novo processo para cada foto.

Depois dos testes, acrescente `NSFW_LOCAL_ENABLED=1` às variáveis de ambiente do serviço do bot e reinicie-o. A migração do banco de dados (`20261111_000011`) precisa estar aplicada. Nenhum grupo tem a moderação ativada automaticamente.

No grupo, conceda ao bot permissões para **apagar mensagens** e **banir membros**. O administrador pode usar:

- `/nsfw status`, `/nsfw on`, `/nsfw off`
- `/nsfw fotos on|off`, `/nsfw videos on|off`, `/nsfw gifs on|off`
- `/nsfw sensibilidade balanced|relaxed|strict`

Padrão por grupo: fotos, vídeos e GIFs habilitados, sensibilidade `balanced`. Conteúdo detectado é removido. Banimento ocorre após três infrações com confiança elevada (`>=0.90`) na janela de reincidência implementada. Os administradores não são punidos automaticamente. Vídeos e GIFs têm somente 3 quadros amostrados; arquivos maiores que o limite são ignorados.

O modelo é carregado uma única vez e executado fora do event loop. Uma fila de moderação separada impede que a análise use a fila de downloads, mas o processamento ainda usa CPU e RAM da mesma VPS.

**Antes de implantar:** valide o código e o modelo na VPS, teste em um grupo controlado e considere um modo somente aviso para classificação ambígua. A rede neural pode cometer falsos positivos ou deixar passar conteúdo.
