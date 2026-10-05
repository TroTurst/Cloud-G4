#!/usr/bin/env bash
# Preparación única de cada worker (server1..server4). Ejecutar como root en cada servidor.
# Uso: sudo ./preparar_servidor.sh <interfaz_de_datos> [bridge]
#   ej.: sudo ./preparar_servidor.sh ens4 br-int
set -euo pipefail
IF_DATOS=${1:?indicar la interfaz conectada a la red de datos (OFS), p. ej. ens4}
BR=${2:-br-int}

apt-get update -y
apt-get install -y qemu-system-x86 qemu-utils openvswitch-switch
systemctl enable --now openvswitch-switch

# Bridge de datos: la interfaz física queda como troncal (sin etiqueta = acepta todas las VLAN).
ovs-vsctl --may-exist add-br "$BR"
ovs-vsctl --may-exist add-port "$BR" "$IF_DATOS"
ip addr flush dev "$IF_DATOS" || true
ip link set "$IF_DATOS" up
ip link set "$BR" up

mkdir -p /var/lib/orq/imagenes /var/lib/orq/vms

if [ -e /dev/kvm ]; then
  echo "KVM disponible."
else
  echo "AVISO: /dev/kvm no existe. Poner \"kvm\": false en config/cluster.json (QEMU por software, más lento)."
fi
echo "Servidor listo: bridge $BR con troncal $IF_DATOS."
