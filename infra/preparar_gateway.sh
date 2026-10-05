#!/usr/bin/env bash
# Preparación única del gateway (donde corre el orquestador). Ejecutar como root.
# Uso: sudo ./preparar_gateway.sh <interfaz_de_datos> <usuario_ssh_de_los_servidores>
#   ej.: sudo ./preparar_gateway.sh ens4 ubuntu
set -euo pipefail
IF_DATOS=${1:?indicar la interfaz conectada a la red de datos (OFS), p. ej. ens4}
USUARIO=${2:-ubuntu}

apt-get update -y
apt-get install -y python3 dnsmasq-base iptables cloud-image-utils genisoimage openssh-client wget
sysctl -w net.ipv4.ip_forward=1
grep -q '^net.ipv4.ip_forward=1' /etc/sysctl.conf || echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf
ip link set "$IF_DATOS" up

# Imágenes base (se copian a los servidores bajo demanda)
mkdir -p /opt/orq/imagenes
cd /opt/orq/imagenes
[ -f cirros-0.6.2-x86_64-disk.img ] || wget -q https://download.cirros-cloud.net/0.6.2/cirros-0.6.2-x86_64-disk.img
[ -f jammy-server-cloudimg-amd64.img ] || wget -q https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img

cat <<MSG
Gateway listo. Pasos manuales restantes (como el usuario que ejecutará el orquestador):
  1. ssh-keygen -t rsa -f ~/.ssh/id_rsa -N ''           (si no existe)
  2. for i in 1 2 3 4; do ssh-copy-id ${USUARIO}@10.0.10.\$i; done
  3. En cada servidor: el usuario ${USUARIO} debe tener sudo sin contraseña
     (echo "${USUARIO} ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/orq)
  4. El usuario que corre el orquestador en el gateway también necesita sudo sin contraseña.
MSG
