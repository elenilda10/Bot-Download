# Mensagens de transferência por plataforma

O status de download usa o emoji personalizado `5435977112538324435`; no envio,
troca para o foguete `5866355487255039002`. A mesma mensagem muda de
`⬇️ X/Twitter: Baixando conteúdo…` para `🚀 X/Twitter: Enviando para o Telegram…`. Fotos, vídeos, álbuns,
áudio e documentos usam o mesmo texto. Português e inglês são preservados.

As funções centralizadas em `messages/user_messages.py` aceitam `platform` como
argumento opcional; sem ele, conservam o comportamento anterior. Os handlers
passam o nome da plataforma e editam a mensagem já criada. O Twitter agora
atualiza o status antes de enviar a mídia. O progresso existente mantém seus
números e intervalos, usando o novo cabeçalho. Links do Universal usam “Link”.

Para instalar na VPS, execute o script a partir desta revisão:

```bash
/root/Bot-Download/venv/bin/python scripts/install_platform_status_vps.py --target /root/Bot-Download
/root/Bot-Download/venv/bin/python scripts/install_platform_status_vps.py --target /root/Bot-Download --apply
```

O instalador adapta as funções e chamadas dos arquivos presentes no destino,
valida a sintaxe antes de escrever e salva backups em `backups/platform-status-*`.
Não muda `.env`, não reinicia o serviço e preserva o restante dos handlers locais.
A troca dos emojis precisa ser conferida no Telegram após a instalação.

Verificação isolada: `python tests/test_platform_status.py`.
