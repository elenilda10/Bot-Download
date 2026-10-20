#!/usr/bin/env bash
# Start WireGuard in the host namespace, then place its interface in ph-proton.
# WireGuard's encrypted UDP socket remains in the original (host) namespace.
set -Eeuo pipefail

NS=ph-proton
WG=protonbot
ADDR=10.2.0.2/32
CONF=/etc/wireguard/protonbot.conf
NS_ETC="/etc/netns/$NS"

host_has_wg() { ip link show dev "$WG" >/dev/null 2>&1; }
ns_has_wg() { ip -n "$NS" link show dev "$WG" >/dev/null 2>&1; }

start() {
    if host_has_wg; then
        echo "ERRO: $WG ainda existe na rede principal; pare o wg-quick antigo." >&2
        return 1
    fi
    if ns_has_wg; then
        echo "ERRO: $WG ja existe em $NS; verifique o estado antes." >&2
        return 1
    fi
    test -f "$CONF" || { echo "Configuracao WireGuard ausente" >&2; return 1; }
    if ! ip netns list | awk '{print $1}' | grep -qx "$NS"; then
        ip netns add "$NS"
    fi
    ip -n "$NS" link set lo up

    install -d -m 755 "$NS_ETC"
    printf 'nameserver 10.2.0.1\noptions timeout:2 attempts:2\n' > "$NS_ETC/resolv.conf"
    # Avoid nss-resolve connecting to the host's systemd-resolved via UNIX socket.
    # ip netns exec bind-mounts both files for processes inside the namespace.
    awk '/^hosts:/ {print "hosts: files dns"; found=1; next} {print} END {if (!found) print "hosts: files dns"}' \
        /etc/nsswitch.conf > "$NS_ETC/nsswitch.conf"

    cleanup_start_error() {
        ip -n "$NS" link del dev "$WG" 2>/dev/null || true
        ip link del dev "$WG" 2>/dev/null || true
    }
    trap cleanup_start_error ERR
    ip link add dev "$WG" type wireguard
    wg setconf "$WG" <(wg-quick strip "$CONF")
    ip link set dev "$WG" netns "$NS"
    ip -n "$NS" addr add "$ADDR" dev "$WG"
    ip -n "$NS" link set dev "$WG" mtu 1420 up
    ip -n "$NS" route replace default dev "$WG"
    ip -n "$NS" route get 1.1.1.1 | grep -Fq "dev $WG"
    trap - ERR
    echo "Interface isolada $WG iniciada no namespace $NS."
}

stop() {
    if ip netns list | awk '{print $1}' | grep -qx "$NS"; then
        ip -n "$NS" link del dev "$WG" 2>/dev/null || true
    fi
    echo "Interface isolada $WG parada; namespace preservado."
}

case "${1:-}" in
    start) start ;;
    stop) stop ;;
    *) echo "Uso: $0 start|stop" >&2; exit 2 ;;
esac
