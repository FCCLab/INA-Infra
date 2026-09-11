#!/usr/bin/env bash
# DISABLED. Host-wide TCP blast (rmem_max, tcp_adv_win_scale=-31, initcwnd 10000)
# took down bare-metal usrp and gpu-a40 (SSH hung at KEX).
#
# TCP is pod-netns only now: applications/exp4/common/ifaces.sh
#   server: exp4_tune_tcp + exp4_tune_tcp_iface on to-client when up
#   client: exp4_tune_tcp + exp4_tune_tcp_iface on to-server when detected
set -euo pipefail
echo "tune_tcp_hosts.sh is disabled (pod-only TCP in ifaces.sh). Not changing host sysctls." >&2
exit 0
