# Audible individual por usuário

Requer Linux, LibationCli 14.2.5 com `login-external`, `scan` e `liberate`, FFmpeg
e FFprobe disponíveis no PATH do serviço. Não utiliza a conta de teste da VPS,
Firefox ou um arquivo de cookies compartilhado. Não precisa de pacotes Python
adicionais. A integração usa o mesmo fluxo de navegador externo testado na VPS.

## Uso

1. Enviar um link público de livro Audible no privado. Se não houver sessão,
   o bot explica a necessidade do login e pede consentimento pelo botão Conectar.
2. Informar o e-mail da conta e abrir o botão para entrar na página da Amazon.
   Senha e código de verificação são informados somente no navegador da Amazon.
3. Copiar a URL final após a autorização e enviá-la no privado. O bot tenta
   apagar o e-mail e a URL recebidos. O código vai somente ao processo de login
   daquele usuário; não é passado em argumentos de subprocessos nem registrado
   pelo handler nos logs. Telegram ainda processa essas mensagens: não é um
   fluxo de ponta a ponta nem um serviço OAuth oficial da Amazon.
4. O livro solicitado é retomado após o login. A conta deve ter acesso ao título
   em sua biblioteca; um link sozinho não libera o acesso.

`/audible` abre o menu para o Brasil. `/audible us` (ou br, uk, de, fr, it, es,
ca, au, jp, in) permite selecionar o mercado. Links identificam seu próprio
mercado. `/audible_logout` ou Desconectar apaga todas as sessões locais daquele
usuário, incluindo os arquivos baixados, e cancela operações em andamento.
A revogação do dispositivo remoto pode ser feita nas configurações da Amazon.
Links enviados em grupos oferecem um botão para continuar no privado.
Autorização e entrega em mensagens Business não são habilitadas.

## Isolamento e limites

- Diretório padrão fora do repositório:
  `~/.local/share/savevid-audible/users/<telegram_id>/<market>/`.
- `AUDIBLE_DATA_DIR` substitui a raiz; `LIBATION_CLI` substitui o executável.
- Cada perfil tem biblioteca/banco próprios, `HOME` e XDG isolados, diretórios
  com modo 0700 e chave de 32 bytes própria com modo 0600. Libation recebe essa
  chave pelo caminho `LIBATION_MASTER_KEY_FILE` e a preferência de tokens
  `Encrypted`. Não se reutilizam contas/configurações globais do operador.
  Quem administra o servidor tem acesso aos arquivos e chaves locais.
- Sem cache de áudio ou file_id compartilhado entre usuários.
- Até quatro logins pendentes e dois downloads/envios; uma operação de download
  por usuário. O estado é coordenado dentro de um processo do serviço.
- Login pendente expira em dez minutos; é interrompido no cancelamento. Após
  reiniciar o serviço, começar novamente os logins ainda não concluídos.
  Sessões já autorizadas permanecem até logout ou expiração pela origem.
- Scan limitado a 180 segundos; download e preparação a 1200 segundos por etapa.
  Subprocessos são assíncronos, com saída drenada e limitada em memória. Timeout
  ou cancelamento encerra o grupo de processos, incluindo processos filhos.
- Só se executa `liberate --id <ASIN validado>`. Antes de cada operação removem-se
  mídias antigas/parciais para não enviar um arquivo abandonado após reinício.
  O resultado deve incluir exatamente o ASIN pedido e um áudio válido.
- AAC é copiado para M4A sem recodificação. Outros codecs são convertidos para
  AAC a 64 kb/s. Capa e capítulos não são incorporados nessa saída para Telegram.
- O tamanho final respeita `MAX_FILE_SIZE`. Há verificação periódica de espaço
  por operação (três vezes esse limite para download e temporários). Não é uma
  cota global de disco do servidor. Toda mídia temporária é removida após envio,
  falha ou cancelamento normal; após encerramento abrupto, é limpa na próxima
  operação do mesmo perfil ou ao desconectar.
- Usa a sessão comum do bot, incluindo tratamento de 429 do Telegram.
- Logs detalhados do Libation são direcionados a `/dev/null`; o handler não
  repassa diagnóstico bruto, credenciais ou URLs de retorno ao usuário.

As mensagens de consentimento, ajuda e erros estão em PT, EN, ES e DE.

## Instalação

`python scripts/install_audible_vps.py --target /root/Bot-Download --apply --diagnose`

O instalador valida e faz backups; preserva alterações locais compatíveis e não
altera `.env`, sessões existentes ou o estado do serviço. Reiniciar
`bot-download.service` após instalar. Não copiar os arquivos da conta do teste
em `/root/libation-teste/config` para os perfis do bot.

Teste operacional: usar duas contas Telegram distintas, conectar cada uma pelo
próprio navegador e verificar que um livro presente só em uma biblioteca não é
entregue pela sessão da outra. Os testes automatizados usam um processo CLI
simulado com PTY e arquivos temporários, sem autenticar contas reais, além de
um teste de preparação com FFmpeg/FFprobe reais.

## Interface e envio compartilhados

O aviso inicial responde ao link do livro e é editado durante e-mail, login no
navegador, download e upload. A referência ao link original é mantida separada
das mensagens sensíveis apagadas. Ao terminar o envio, o aviso é removido.
O bot não cria uma mensagem adicional de confirmação de envio.

A entrega usa `run_audio_flow` e `send_audio_with_thumbnail`, como os outros
handlers de áudio, com legenda conforme configurações, duração e thumbnail de
fallback do bot. O arquivo responde ao link original quando disponível, nunca à
URL de autorização. O cache da pipeline é explicitamente desabilitado para
continuar verificando a licença individual e não compartilhar file_ids.
O instalador escolhe a atualização incremental quando o handler já existe.
