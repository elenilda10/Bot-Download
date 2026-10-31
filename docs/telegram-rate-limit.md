# Controle de envios e respostas 429 do Telegram

`TelegramRateLimitMiddleware` é registrado na sessão criada por `main.create_app`.
Ele cobre os handlers que usam essa sessão, incluindo envios de arquivos e mídia
em cache, álbuns e edições de progresso. Sessões HTTP criadas fora dela não são
interceptadas.

- Uma resposta `TelegramRetryAfter` repete a mesma requisição após `retry_after`
  mais 250 ms de margem. Até três novas tentativas, com até 300 segundos de
  espera acumulada por requisição. Restrições maiores continuam registradas na
  conversa e são propagadas sem fazer novas requisições prematuras.
- Envios e edições para uma conversa compartilham uma fila. A espera usa
  `asyncio.sleep`; nenhuma trava global permanece adquirida durante o cooldown
  ou a transferência. Outras conversas e respostas a callbacks podem continuar.
- Espaçamento preventivo: 1,05 s por destino privado e 3,1 s para IDs negativos
  ou nomes de canais/grupos. Álbuns e cópias/encaminhamentos múltiplos reservam
  o número de mensagens correspondente. Uma reserva global curta limita o ritmo
  a cerca de 25 mensagens por segundo. São valores conservadores, não garantia
  contra limites adicionais/dinâmicos impostos pelo Telegram.
- Cancelar uma tarefa libera sua trava. O cache descarta destinos ociosos quando
  excede 4096 entradas; tarefas ativas e cooldowns não são descartados. Não há
  coordenação entre processos; esta implantação executa uma instância do serviço.
- Timeouts de rede, erros 400 e outros erros não são repetidos automaticamente:
  em um timeout, o envio pode ter sido concluído e repeti-lo duplicaria a mídia.
- Se o limite de tentativas/espera se esgotar, o erro segue ao handler. Nas
  coleções, YouTube e Spotify propagam o erro para interromper a coleção e
  conservar os itens restantes, em vez de insistir em todas as faixas.

Instalação incremental a partir de `d62da13`:

```bash
python scripts/install_telegram_rate_limit_vps.py \
  --target /root/Bot-Download --apply --diagnose
systemctl restart bot-download.service
```

O instalador prepara e valida a alteração antes de escrever e cria backups.
Não altera `.env`, credenciais, limites de itens nem a configuração do systemd.
O tratamento é reativo ao erro real retornado pelo Telegram; as restrições não
são removidas pelo bot e uma espera longa pode pausar a conversa afetada.

Referências oficiais:
- https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this
- https://docs.aiogram.dev/en/latest/api/session/middleware.html
