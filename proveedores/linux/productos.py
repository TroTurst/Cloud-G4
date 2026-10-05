"""Productos de la Familia Linux (R2).

Toda la traducción de los recursos genéricos al vocabulario de Linux vive aquí:
QEMU/KVM, interfaces TAP, OVS, QCOW2, cloud-init, iptables, dnsmasq y el switch OFS.
Ningún archivo fuera de proveedores/linux conoce estos términos.

Supuestos de infraestructura (ver README):
- cada servidor tiene un bridge OVS (red_datos.bridge) con su interfaz de datos como troncal;
- el orquestador corre en el gateway, con llave SSH hacia los servidores (usuario con sudo sin contraseña);
- las imágenes base están en imagenes.directorio_local del gateway.
"""
import ipaddress
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import threading

from ..contrato import Proveedor, ErrorPermanente, marca

_BLOQUEOS_IMAGEN = {}
_BLOQUEO_GLOBAL = threading.Lock()


def _bloqueo_imagen(servidor):
    """Un bloqueo por servidor para no copiar ni borrar imágenes base en paralelo."""
    with _BLOQUEO_GLOBAL:
        return _BLOQUEOS_IMAGEN.setdefault(servidor, threading.Lock())


def nombre_tap(p, indice):
    return f"t{p['slice']}n{p['nodo_indice']:02d}i{indice}"


def nombre_vlanif(cfg, vlan):
    return f"{cfg['gateway']['if_datos']}.{vlan}"


def _limpiar_iptables(marca_propiedad):
    """Script que borra todas las reglas de iptables con la marca dada (idempotente)."""
    return f"""
for t in nat filter; do
  iptables -t $t -S | grep -E -- "--comment \\"?{marca_propiedad}\\"?( |$)" | sed 's/^-A /-D /' | while read -r regla; do
    eval "iptables -t $t $regla" || true
  done
done
"""


class _Base(Proveedor):
    def __init__(self, cfg, remoto, log):
        self.cfg = cfg
        self.r = remoto
        self.log = log

    def _dir_vm(self, vm):
        return f"{self.cfg['vms']['directorio_remoto']}/{vm}"


# ======================================================================= CONMUTACIÓN
class ConmutacionLinux(_Base):
    """Permite la VLAN de un segmento en el OFS solo entre los puertos de los equipos que participan.
    modo 'ninguno': se asume que el OFS ya transporta las VLANs del grupo (troncales / modo NORMAL)."""
    tipo = "conmutacion"

    def _puertos(self, p):
        por_nombre = {s["nombre"]: s["puerto_ofs"] for s in self.cfg["servidores"]}
        puertos = sorted({por_nombre[s] for s in p["servidores"]})
        if p.get("incluye_salida"):
            puertos.append(self.cfg["gateway"]["puerto_ofs"])
        return puertos

    def _cookie(self, vlan):
        return f"0x{0x0E000000 + vlan:x}"

    def crear(self, rec):
        p, ofs = rec["params"], self.cfg["ofs"]
        puertos = self._puertos(p)
        if ofs["modo"] == "ninguno" or len(puertos) < 2:
            return {"vlan": p["vlan"], "flujos": 0, "modo": ofs["modo"]}
        lineas = []
        for ent in puertos:
            salidas = ",".join(f"output:{s}" for s in puertos if s != ent)
            flujo = f"cookie={self._cookie(p['vlan'])},priority=200,in_port={ent},dl_vlan={p['vlan']},actions={salidas}"
            lineas.append(f"ovs-ofctl -O {ofs['protocolo']} add-flow {ofs['bridge']} {shlex.quote(flujo)}")
        self.r.ejecutar("ofs", "\n".join(lineas), f"permitir VLAN {p['vlan']} en OFS")
        return {"vlan": p["vlan"], "flujos": len(lineas), "modo": ofs["modo"]}

    def leer(self, rec, real):
        if self.cfg["ofs"]["modo"] == "ninguno" or not real.get("flujos"):
            return real
        ofs, c = self.cfg["ofs"], self._cookie(rec["params"]["vlan"])
        ok = self.r.consultar("ofs", f"ovs-ofctl -O {ofs['protocolo']} dump-flows {ofs['bridge']} cookie={c}/-1 "
                                     f"| grep -q dl_vlan && echo SI || echo NO", "leer flujos")
        return real if ok else None

    def destruir(self, rec, real):
        if self.cfg["ofs"]["modo"] == "ninguno" or not (real or {}).get("flujos"):
            return
        ofs, c = self.cfg["ofs"], self._cookie(rec["params"]["vlan"])
        self.r.ejecutar("ofs", f"ovs-ofctl -O {ofs['protocolo']} del-flows {ofs['bridge']} cookie={c}/-1",
                        f"retirar VLAN {rec['params']['vlan']} del OFS")


# ======================================================================= RED
class RedLinux(_Base):
    """Segmento = VLAN. Los segmentos de enlace se realizan con las etiquetas de los puertos (no hay
    nada que crear aparte). El segmento de acceso se termina en el gateway: subinterfaz VLAN,
    DHCP con direcciones fijas por MAC, NAT de salida y aislamiento respecto de otros slices."""
    tipo = "red"

    def crear(self, rec):
        p = rec["params"]
        if not p.get("acceso"):
            return {"vlan": p["vlan"]}
        gw, m = self.cfg["gateway"], marca(p["slice"], rec["id"])
        vif = nombre_vlanif(self.cfg, p["vlan"])
        red = ipaddress.ip_network(p["subred"])
        hosts = " ".join(f"--dhcp-host={h['mac']},{h['ip']}" for h in p["hosts"])
        pidf, lease = f"/run/orq-dnsmasq-{p['slice']}.pid", f"/run/orq-dnsmasq-{p['slice']}.leases"
        script = f"""
sysctl -qw net.ipv4.ip_forward=1
ip link show {vif} >/dev/null 2>&1 || ip link add link {gw['if_datos']} name {vif} type vlan id {p['vlan']}
ip addr replace {p['puerta']}/{red.prefixlen} dev {vif}
ip link set {gw['if_datos']} up
ip link set {vif} up
if [ -f {pidf} ] && kill -0 $(cat {pidf}) 2>/dev/null; then :; else
  dnsmasq --interface={vif} --bind-interfaces --except-interface=lo --port=0 \\
    --dhcp-range={red[2]},{red[-2]},{red.netmask},12h {hosts} \\
    --dhcp-option=3,{p['puerta']} --dhcp-option=6,8.8.8.8,1.1.1.1 \\
    --pid-file={pidf} --dhcp-leasefile={lease}
fi
{_limpiar_iptables(m)}
iptables -t nat -A POSTROUTING -s {red} -o {gw['if_externa']} -j MASQUERADE -m comment --comment {m}
iptables -I FORWARD 1 -i {vif} -j DROP -m comment --comment {m}
iptables -I FORWARD 1 -i {gw['if_externa']} -o {vif} -m state --state RELATED,ESTABLISHED -j ACCEPT -m comment --comment {m}
iptables -I FORWARD 1 -i {vif} -o {gw['if_externa']} -j ACCEPT -m comment --comment {m}
"""
        self.r.ejecutar("gateway", script, f"red de acceso VLAN {p['vlan']}")
        return {"vlan": p["vlan"], "interfaz": vif, "subred": p["subred"], "puerta": p["puerta"]}

    def leer(self, rec, real):
        p = rec["params"]
        if not p.get("acceso"):
            return real
        vif = nombre_vlanif(self.cfg, p["vlan"])
        ok = self.r.consultar("gateway", f"ip link show {vif} >/dev/null 2>&1 && echo SI || echo NO", "leer red de acceso")
        return real if ok else None

    def destruir(self, rec, real):
        p = rec["params"]
        if not p.get("acceso"):
            return
        vif = nombre_vlanif(self.cfg, p["vlan"])
        pidf = f"/run/orq-dnsmasq-{p['slice']}.pid"
        script = f"""
if [ -f {pidf} ]; then kill $(cat {pidf}) 2>/dev/null || true; rm -f {pidf}; fi
rm -f /run/orq-dnsmasq-{p['slice']}.leases
{_limpiar_iptables(marca(p['slice'], rec['id']))}
ip link del {vif} 2>/dev/null || true
"""
        self.r.ejecutar("gateway", script, f"eliminar red de acceso VLAN {p['vlan']}")


# ======================================================================= VOLUMEN
class VolumenLinux(_Base):
    """Disco de la VM = capa QCOW2 sobre una imagen base compartida por servidor.
    La imagen base se copia bajo demanda y se borra cuando ninguna VM del servidor la usa.
    Genera además el disco de configuración inicial (cloud-init NoCloud)."""
    tipo = "volumen"
    atributos_inmutables = ("imagen",)

    def _base(self, imagen):
        cat = self.cfg["imagenes"]
        return f"{cat['directorio_remoto']}/{cat['catalogo'][imagen]['archivo']}"

    def validar(self, rec):
        img = rec["params"]["imagen"]
        if img not in self.cfg["imagenes"]["catalogo"]:
            raise ErrorPermanente(f"imagen '{img}' no está en el catálogo")
        local = os.path.join(self.cfg["imagenes"]["directorio_local"],
                             self.cfg["imagenes"]["catalogo"][img]["archivo"])
        if not self.cfg.get("modo_seco") and not os.path.exists(local):
            raise ErrorPermanente(f"no existe la imagen local {local}")

    def _asegurar_base(self, servidor, imagen):
        base = self._base(imagen)
        with _bloqueo_imagen(servidor):
            if self.r.consultar(servidor, f"[ -f {base} ] && echo SI || echo NO", "¿imagen base presente?"):
                return False
            local = os.path.join(self.cfg["imagenes"]["directorio_local"],
                                 self.cfg["imagenes"]["catalogo"][imagen]["archivo"])
            self.log.info("copiando imagen base %s a %s", imagen, servidor)
            self.r.copiar(local, servidor, base, f"imagen {imagen}")
            return True

    def _semilla(self, p):
        """Genera seed.iso (NoCloud) localmente: credenciales, nombre y direcciones por MAC."""
        img = self.cfg["imagenes"]["catalogo"][p["imagen"]]
        usuario = {"hostname": p["nodo"], "password": self.cfg["vms"]["contrasena"],
                   "chpasswd": {"expire": False}, "ssh_pwauth": True}
        if self.cfg["vms"].get("llave_publica"):
            usuario["ssh_authorized_keys"] = [self.cfg["vms"]["llave_publica"]]
        ethernets = {}
        for k, itf in enumerate(p["interfaces"]):
            nombre = f"eth{k}"
            conf = {"match": {"macaddress": itf["mac"]}, "set-name": nombre}
            if itf.get("dhcp"):
                conf["dhcp4"] = True
            elif itf.get("direccion"):
                conf["addresses"] = [itf["direccion"]]
            else:
                conf["dhcp4"] = False
            ethernets[nombre] = conf
        carpeta = tempfile.mkdtemp(prefix="orq-semilla-")
        with open(os.path.join(carpeta, "user-data"), "w") as f:
            f.write("#cloud-config\n" + json.dumps(usuario, indent=1) + "\n")
        with open(os.path.join(carpeta, "meta-data"), "w") as f:
            f.write(f"instance-id: {p['vm']}\nlocal-hostname: {p['nodo']}\n")
        with open(os.path.join(carpeta, "network-config"), "w") as f:
            f.write(json.dumps({"version": 2, "ethernets": ethernets}, indent=1) + "\n")
        iso = os.path.join(carpeta, "seed.iso")
        if self.cfg.get("modo_seco"):
            self.log.info("[seco] semilla de %s (%s): %s", p["vm"], img["usuario"], json.dumps(ethernets))
            return iso, carpeta
        if shutil.which("cloud-localds"):
            cmd = ["cloud-localds", "-N", os.path.join(carpeta, "network-config"), iso,
                   os.path.join(carpeta, "user-data"), os.path.join(carpeta, "meta-data")]
        elif shutil.which("genisoimage"):
            cmd = ["genisoimage", "-quiet", "-output", iso, "-volid", "cidata", "-joliet", "-rock",
                   "user-data", "meta-data", "network-config"]
        else:
            raise ErrorPermanente("se necesita cloud-localds o genisoimage en el orquestador")
        r = subprocess.run(cmd, cwd=carpeta, capture_output=True, text=True)
        if r.returncode != 0:
            raise ErrorPermanente(f"no se pudo generar la semilla: {r.stderr}")
        return iso, carpeta

    def crear(self, rec):
        p, srv = rec["params"], rec["servidor"]
        base, d = self._base(p["imagen"]), self._dir_vm(p["vm"])
        copiada = self._asegurar_base(srv, p["imagen"])
        iso, carpeta = self._semilla(p)
        try:
            self.r.copiar(iso, srv, f"{d}/seed.iso", "semilla cloud-init")
        finally:
            shutil.rmtree(carpeta, ignore_errors=True)
        tam_mb = int(float(p["disco_gb"]) * 1024)
        script = f"""
mkdir -p {d}
if [ ! -f {d}/disco.qcow2 ]; then
  qemu-img create -q -f qcow2 -F qcow2 -b {base} {d}/disco.qcow2
  qemu-img resize -q {d}/disco.qcow2 {tam_mb}M 2>/dev/null || true
fi
"""
        with _bloqueo_imagen(srv):
            self.r.ejecutar(srv, script, f"disco de {p['vm']}")
        return {"disco": f"{d}/disco.qcow2", "semilla": f"{d}/seed.iso", "base": base,
                "imagen_copiada": copiada}

    def leer(self, rec, real):
        d = self._dir_vm(rec["params"]["vm"])
        ok = self.r.consultar(rec["servidor"], f"[ -f {d}/disco.qcow2 ] && echo SI || echo NO", "leer disco")
        return real if ok else None

    def destruir(self, rec, real):
        p, srv = rec["params"], rec["servidor"]
        base, d, raiz = self._base(p["imagen"]), self._dir_vm(p["vm"]), self.cfg["vms"]["directorio_remoto"]
        # Borrado inteligente: si ninguna otra VM del servidor usa la imagen base, se elimina.
        script = f"""
rm -rf {d}
en_uso=0
for f in {raiz}/*/disco.qcow2; do
  [ -e "$f" ] || continue
  if qemu-img info -U "$f" 2>/dev/null | grep -q "backing file: {base}"; then en_uso=1; break; fi
done
if [ $en_uso -eq 0 ] && [ -f {base} ]; then rm -f {base}; echo IMAGEN_BORRADA; fi
"""
        with _bloqueo_imagen(srv):
            salida = self.r.ejecutar(srv, script, f"eliminar disco de {p['vm']}")
        if "IMAGEN_BORRADA" in (salida or ""):
            self.log.info("borrado inteligente: imagen %s eliminada de %s", p["imagen"], srv)


# ======================================================================= PUERTO
class PuertoLinux(_Base):
    """Interfaz TAP de la VM conectada al bridge OVS con la VLAN de su segmento (puerto de acceso)."""
    tipo = "puerto"
    atributos_inmutables = ("vlan",)

    def crear(self, rec):
        p = rec["params"]
        tap, br = nombre_tap(p, p["indice"]), self.cfg["red_datos"]["bridge"]
        m = marca(p["slice"], rec["id"])
        script = f"""
ip link show {tap} >/dev/null 2>&1 || ip tuntap add mode tap name {tap}
ip link set {tap} up
ovs-vsctl --may-exist add-port {br} {tap} tag={p['vlan']} -- set interface {tap} external_ids:orq-marca={m}
"""
        self.r.ejecutar(rec["servidor"], script, f"puerto {tap} VLAN {p['vlan']}")
        return {"tap": tap, "vlan": p["vlan"], "bridge": br}

    def leer(self, rec, real):
        tap = nombre_tap(rec["params"], rec["params"]["indice"])
        br = self.cfg["red_datos"]["bridge"]
        ok = self.r.consultar(rec["servidor"], f"ovs-vsctl list-ports {br} | grep -qx {tap} && echo SI || echo NO",
                              "leer puerto")
        return real if ok else None

    def destruir(self, rec, real):
        tap = nombre_tap(rec["params"], rec["params"]["indice"])
        br = self.cfg["red_datos"]["bridge"]
        self.r.ejecutar(rec["servidor"],
                        f"ovs-vsctl --if-exists del-port {br} {tap}\nip link del {tap} 2>/dev/null || true",
                        f"eliminar puerto {tap}")

    def adoptar(self, marca_propiedad):
        return []  # en esta versión la idempotencia se logra con --may-exist


# ======================================================================= CÓMPUTO
class ComputoLinux(_Base):
    """VM ejecutada con QEMU/KVM como proceso en segundo plano, con sus interfaces TAP y consola VNC."""
    tipo = "computo"
    atributos_inmutables = ("imagen",)

    def crear(self, rec):
        p, d = rec["params"], self._dir_vm(rec["params"]["vm"])
        acel = "-enable-kvm -cpu host" if self.cfg["vms"].get("kvm", True) else "-accel tcg"
        redes = " ".join(
            f"-netdev tap,id=n{i['indice']},ifname={nombre_tap(p, i['indice'])},script=no,downscript=no "
            f"-device virtio-net-pci,netdev=n{i['indice']},mac={i['mac']}"
            for i in p["interfaces"])
        script = f"""
if [ -f {d}/qemu.pid ] && kill -0 $(cat {d}/qemu.pid) 2>/dev/null; then echo YA_EXISTE; exit 0; fi
qemu-system-x86_64 {acel} -name {p['vm']},process={p['vm']} -smp {p['vcpu']} -m {p['ram_mb']} \\
  -drive file={d}/disco.qcow2,if=virtio,format=qcow2 \\
  -drive file={d}/seed.iso,media=cdrom,format=raw,readonly=on \\
  {redes} \\
  -vnc 0.0.0.0:{p['consola']} -daemonize -pidfile {d}/qemu.pid
"""
        self.r.ejecutar(rec["servidor"], script, f"arrancar {p['vm']}")
        return {"vm": p["vm"], "pidfile": f"{d}/qemu.pid", "vnc": 5900 + p["consola"]}

    def leer(self, rec, real):
        d = self._dir_vm(rec["params"]["vm"])
        ok = self.r.consultar(rec["servidor"],
                              f"[ -f {d}/qemu.pid ] && kill -0 $(cat {d}/qemu.pid) 2>/dev/null && echo SI || echo NO",
                              "leer VM")
        return real if ok else None

    def destruir(self, rec, real):
        d = self._dir_vm(rec["params"]["vm"])
        script = f"""
if [ -f {d}/qemu.pid ]; then
  pid=$(cat {d}/qemu.pid)
  kill $pid 2>/dev/null || true
  for i in $(seq 1 20); do kill -0 $pid 2>/dev/null || break; sleep 0.5; done
  kill -9 $pid 2>/dev/null || true
  rm -f {d}/qemu.pid
fi
"""
        self.r.ejecutar(rec["servidor"], script, f"detener {rec['params']['vm']}")


# ======================================================================= ACCESO EXTERNO
class AccesoLinux(_Base):
    """Entrada SSH desde fuera: puerto externo del gateway redirigido al puerto 22 de la VM."""
    tipo = "acceso"

    def crear(self, rec):
        p, gw = rec["params"], self.cfg["gateway"]
        m, vif = marca(p["slice"], rec["id"]), nombre_vlanif(self.cfg, p["vlan"])
        script = f"""
{_limpiar_iptables(m)}
iptables -t nat -A PREROUTING -i {gw['if_externa']} -p tcp --dport {p['puerto_externo']} -j DNAT --to-destination {p['ip']}:22 -m comment --comment {m}
iptables -I FORWARD 1 -i {gw['if_externa']} -o {vif} -p tcp -d {p['ip']} --dport 22 -j ACCEPT -m comment --comment {m}
"""
        self.r.ejecutar("gateway", script, f"acceso SSH a {p['vm']}")
        usuario = self.cfg["imagenes"]["catalogo"][p["imagen"]]["usuario"]
        return {"ssh": f"ssh -p {p['puerto_externo']} {usuario}@{gw['ip_externa']}", "ip_interna": p["ip"]}

    def leer(self, rec, real):
        m = marca(rec["params"]["slice"], rec["id"])
        ok = self.r.consultar("gateway", f"iptables -t nat -S | grep -q -- '{m}' && echo SI || echo NO", "leer acceso")
        return real if ok else None

    def destruir(self, rec, real):
        self.r.ejecutar("gateway", _limpiar_iptables(marca(rec["params"]["slice"], rec["id"])),
                        f"eliminar acceso de {rec['params']['vm']}")


# ======================================================================= CONSOLA
class ConsolaLinux(_Base):
    """Consola VNC alcanzable solo mediante un túnel SSH a través del gateway.
    No crea nada en la infraestructura: entrega las instrucciones de acceso."""
    tipo = "consola"

    def crear(self, rec):
        p, gw = rec["params"], self.cfg["gateway"]
        ip = {s["nombre"]: s["ip"] for s in self.cfg["servidores"]}[rec["servidor"]]
        puerto = 5900 + p["consola"]
        return {"tunel": f"ssh -L {puerto}:{ip}:{puerto} <usuario>@{gw['ip_externa']}",
                "vnc": f"localhost:{puerto}"}

    def leer(self, rec, real):
        return real

    def destruir(self, rec, real):
        return None
