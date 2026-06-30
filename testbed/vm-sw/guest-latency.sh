#!/bin/sh
# Netem delay on switch ports. Run after guest-bridge.sh (or from it post-enslave).
# SW_LATENCY="inf-lower:10ms"
# SW_LATENCY_PHASE=egress|ingress|all|bridge (default all)
#
# On Linux bridge member ports, ingress ifb redirect does not shape forwarded
# frames (only egress netem applies). For symmetric RTT use 2x egress delay on
# enslaved ports (10ms each way -> 20ms egress on inf-lower).
set -e

PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH

SW_LATENCY="${SW_LATENCY:-}"
SW_LATENCY_PHASE="${SW_LATENCY_PHASE:-all}"

[ -n "$SW_LATENCY" ] || exit 0

if command -v tc >/dev/null 2>&1; then
  TC=tc
elif [ -x /sbin/tc ]; then
  TC=/sbin/tc
elif [ -x /usr/sbin/tc ]; then
  TC=/usr/sbin/tc
else
  echo "vm-sw: tc not found, installing iproute2..." >&2
  apk add --no-cache iproute2 kmod 2>/dev/null || true
  if command -v tc >/dev/null 2>&1; then
    TC=tc
  elif [ -x /sbin/tc ]; then
    TC=/sbin/tc
  elif [ -x /usr/sbin/tc ]; then
    TC=/usr/sbin/tc
  else
    echo "vm-sw: tc not found (apk add iproute2) — skipping latency" >&2
    exit 0
  fi
fi

is_bridge_port() {
  ip link show "$1" 2>/dev/null | grep -q 'master '
}

delay_to_ms() {
  local d="$1" n
  case "$d" in
    *ms) n="${d%ms}" ;;
    *us) n=$(( ${d%us} / 1000 )) ;;
    *s) n=$(( ${d%s} * 1000 )) ;;
    *) n="$d" ;;
  esac
  echo "$n"
}

clear_ingress() {
  local iface="$1"
  "$TC" qdisc del dev "$iface" ingress 2>/dev/null || true
  "$TC" filter del dev br0 parent ffff: protocol all pref 10 2>/dev/null || true
}

apply_egress() {
  local iface="$1" delay="$2"
  "$TC" qdisc replace dev "$iface" root netem delay "$delay"
}

apply_bridge_symmetric() {
  local iface="$1" delay="$2"
  local ms doubled
  ms="$(delay_to_ms "$delay")"
  doubled=$((ms * 2))
  clear_ingress "$iface"
  apply_egress "$iface" "${doubled}ms"
  echo "vm-sw: latency ${delay} each way as ${doubled}ms egress on bridged $iface"
}

ifb_idx=0
for spec in $SW_LATENCY; do
  iface="${spec%%:*}"
  delay="${spec#*:}"
  [ -n "$iface" ] && [ -n "$delay" ] || continue

  if ! ip link show "$iface" >/dev/null 2>&1; then
    echo "vm-sw: latency skip missing $iface" >&2
    continue
  fi

  case "$SW_LATENCY_PHASE" in
    egress)
      if is_bridge_port "$iface"; then
        apply_bridge_symmetric "$iface" "$delay"
      else
        apply_egress "$iface" "$delay"
        echo "vm-sw: latency $delay egress on $iface"
      fi
      ;;
    bridge|all|*)
      if is_bridge_port "$iface"; then
        apply_bridge_symmetric "$iface" "$delay"
      else
        apply_egress "$iface" "$delay"
        echo "vm-sw: latency $delay egress on $iface (pre-bridge)"
      fi
      ;;
    ingress)
      echo "vm-sw: ingress phase skipped (use bridge/all on vm-sw ports)" >&2
      ;;
  esac

  ifb_idx=$((ifb_idx + 1))
done

exit 0
