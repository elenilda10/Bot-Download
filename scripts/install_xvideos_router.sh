#!/usr/bin/env bash
# Instala o router XVideos sem resetar o worktree de producao.
set -Eeuo pipefail
umask 077
MODE="${1:---check}"
ROOT="/root/Bot-Download"
BRANCH="feature/xvideos-hls-router-20261018"
PY="$ROOT/venv/bin/python"
SERVICE="bot-download.service"
CERT_SOURCE="/tmp/sectigo-teste-ca.pem"
CERT_DEST="$ROOT/certs/xvideos-ca.pem"
FILES=("handlers/xvideos.py" "services/platforms/xvideos_media.py" "tests/test_xvideos_media.py")
case "$MODE" in
  --check|--install) ;;
  *) echo "Uso: bash $0 --check|--install"; exit 2;;
esac
if [[ $(id -u) -ne 0 ]]; then echo "Execute como root."; exit 1; fi
if [[ ! -d "$ROOT/.git" && ! -f "$ROOT/.git" ]]; then
  echo "Repositorio de producao nao encontrado"; exit 1
fi
if [[ ! -x "$PY" || ! -f "$ROOT/.env" ]]; then
  echo "Venv ou .env nao encontrado; nenhuma mudanca aplicada"; exit 1
fi
if ! systemctl is-active --quiet "$SERVICE"; then
  echo "Servico $SERVICE nao esta ativo; nenhuma mudanca aplicada"; exit 1
fi
if [[ ! -s "$CERT_SOURCE" || ! -s /tmp/sectigo-r36.pem ]]; then
  echo "Falta bundle CA temporario validado; nenhuma mudanca aplicada"; exit 1
fi
openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt /tmp/sectigo-r36.pem >/dev/null
WORK="$(mktemp -d -p /tmp xvideos-preparacao.XXXXXXXX)"
trap 'rm -rf -- "$WORK"' EXIT
mkdir -p "$WORK/stage"
echo "=== Buscando branch sem modificar codigo em uso ==="
git -C "$ROOT" fetch --no-tags origin "refs/heads/$BRANCH" >/dev/null
COMMIT="$(git -C "$ROOT" rev-parse FETCH_HEAD)"
STAGE_FILES=()
for file in "${FILES[@]}"; do
  mkdir -p "$WORK/stage/$(dirname "$file")"
  git -C "$ROOT" show "$COMMIT:$file" > "$WORK/stage/$file"
  STAGE_FILES+=("$WORK/stage/$file")
done
echo "Versao: ${COMMIT:0:12}"
echo "=== Sintaxe Python ==="
"$PY" -m py_compile "${STAGE_FILES[@]}"
echo "=== Preferencias de qualidade ==="
(cd "$ROOT" && "$PY" -c 'from services.settings import VIDEO_QUALITY_BEST, VIDEO_QUALITY_BALANCED, VIDEO_QUALITY_SAVER')
echo "=== Testes unitarios ==="
(cd "$ROOT" && "$PY" -m unittest discover -s "$WORK/stage/tests" -p test_xvideos_media.py -v)
echo "=== Registro de routers ==="
"$PY" - "$ROOT/handlers/__init__.py" <<'PY'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text()
if "xvideos.router" not in text:
    lines = [ln for ln in text.splitlines() if ln.startswith("from . import ")]
    if len(lines) != 1 or "universal" not in lines[0] or "    universal.router," not in text:
        raise SystemExit("Formato do registro mudou; abortando para preservar alteracoes locais.")
print("Registro compativel")
PY
if [[ "$MODE" == "--check" ]]; then
  echo "=== PREPARACAO OK ==="
  echo "Nenhum codigo foi alterado e nenhum servico foi reiniciado."
  echo "Para instalar: bash /tmp/install_xvideos_router.sh --install"
  exit 0
fi
echo "=== Backup persistente ==="
BACKUP="/root/Bot-Download-xvideos-backups/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$BACKUP"
ALL_FILES=("${FILES[@]}" "handlers/__init__.py" ".env" "certs/xvideos-ca.pem")
for file in "${ALL_FILES[@]}"; do
  mkdir -p "$BACKUP/$(dirname "$file")"
  if [[ -e "$ROOT/$file" ]]; then
    cp -a -- "$ROOT/$file" "$BACKUP/$file"
  else
    touch "$BACKUP/${file//\//__}.missing"
  fi
done
echo "Backup: $BACKUP"
ROLLBACK=1
restore_all() {
  echo "AVISO: Revertendo instalacao para o backup."
  for file in "${ALL_FILES[@]}"; do
    if [[ -f "$BACKUP/${file//\//__}.missing" ]]; then
      rm -f -- "$ROOT/$file"
    else
      cp -a -- "$BACKUP/$file" "$ROOT/$file"
    fi
  done
  systemctl restart "$SERVICE" || true
}
on_exit() {
  code=$?
  if [[ "$ROLLBACK" -eq 1 ]]; then restore_all; fi
  rm -rf -- "$WORK"
  exit "$code"
}
trap on_exit EXIT
echo "=== Instalando arquivos novos ==="
for file in "${FILES[@]}"; do
  mkdir -p "$ROOT/$(dirname "$file")"
  cp -a -- "$WORK/stage/$file" "$ROOT/$file"
done
"$PY" - "$ROOT/handlers/__init__.py" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text()
if "xvideos.router" not in text:
    lines = [ln for ln in text.splitlines() if ln.startswith("from . import ")]
    if len(lines) != 1 or "universal" not in lines[0]:
        raise SystemExit("Formato do registro de routers desconhecido")
    text = text.replace(lines[0], lines[0].rstrip() + ", xvideos", 1)
    text = text.replace("    universal.router,", "    xvideos.router,\n    universal.router,", 1)
    p.write_text(text)
PY
mkdir -p "$ROOT/certs"
cp -a -- "$CERT_SOURCE" "$CERT_DEST"
chmod 600 "$CERT_DEST"
if ! grep -qE '^[[:space:]]*XVIDEOS_CA_BUNDLE=' "$ROOT/.env"; then
  printf '\nXVIDEOS_CA_BUNDLE=%s\n' "$CERT_DEST" >> "$ROOT/.env"
else
  echo "XVIDEOS_CA_BUNDLE ja definido no .env; valor preservado."
fi
echo "=== Verificando importacao do router ==="
(cd "$ROOT" && "$PY" -m py_compile handlers/__init__.py handlers/xvideos.py services/platforms/xvideos_media.py)
echo "=== Reiniciando somente bot-download.service ==="
systemctl restart "$SERVICE"
sleep 7
if ! systemctl is-active --quiet "$SERVICE"; then
  echo "Bot nao ficou ativo apos reinicio."; exit 1
fi
ROLLBACK=0
echo "=== INSTALACAO CONCLUIDA ==="
echo "Servico ativo: $SERVICE"
echo "Backup: $BACKUP"
echo "Outros servicos nao foram modificados."
