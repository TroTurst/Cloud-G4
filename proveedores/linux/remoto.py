"""Ejecución de comandos en los equipos del cluster a través de la red de gestión.

Cada operación envía un script de bash por la entrada estándar de `ssh <host> sudo -n bash -s`,
lo que evita problemas de comillas. En el gateway, si el orquestador corre ahí, se ejecuta local.

En modo seco no se ejecuta nada: los scripts se registran en el log y las consultas responden
"SI", lo que permite revisar exactamente qué se haría sin tocar la infraestructura.
"""
import os
import shlex
import subprocess

from ..contrato import ErrorPermanente, ErrorTransitorio


class Remoto:
    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.seco = cfg.get("modo_seco", False)
        self.ips = {s["nombre"]: s["ip"] for s in cfg["servidores"]}
        self.ips["gateway"] = cfg["gateway"]["ip_gestion"]
        self.ips["ofs"] = cfg["ofs"]["ip"]

    # ------------------------------------------------------------ comandos
    def _es_local(self, destino):
        return destino == "gateway" and self.cfg["gateway"].get("local", False)

    def _ssh_base(self, ip):
        s = self.cfg["ssh"]
        return ["ssh", "-i", s["llave"], "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                "-o", "StrictHostKeyChecking=accept-new", f"{s['usuario']}@{ip}"]

    def ejecutar(self, destino, script, descripcion=""):
        """Ejecuta el script como root en el destino y devuelve la salida estándar."""
        cuerpo = "set -e\n" + script.strip() + "\n"
        if self.seco:
            self.log.info("[seco] %s @ %s\n%s", descripcion, destino, cuerpo)
            return "SI"
        if self._es_local(destino):
            cmd = ["sudo", "-n", "bash", "-s"]
        else:
            cmd = self._ssh_base(self.ips[destino]) + ["sudo -n bash -s"]
        self.log.debug("%s @ %s\n%s", descripcion, destino, cuerpo)
        try:
            r = subprocess.run(cmd, input=cuerpo, capture_output=True, text=True,
                               timeout=self.cfg["ssh"]["timeout_s"])
        except subprocess.TimeoutExpired as e:
            raise ErrorTransitorio(f"tiempo agotado en {destino}: {descripcion}") from e
        if r.returncode == 255 and not self._es_local(destino):
            raise ErrorTransitorio(f"sin conexión SSH con {destino}: {r.stderr.strip()}")
        if r.returncode != 0:
            raise ErrorPermanente(
                f"fallo en {destino} ({descripcion}), código {r.returncode}: {r.stderr.strip()[-500:]}")
        return r.stdout

    def consultar(self, destino, script, descripcion=""):
        """Ejecuta un script que imprime SI o NO y devuelve True/False."""
        return "SI" in self.ejecutar(destino, script, descripcion)

    def copiar(self, local, destino, ruta_remota, descripcion=""):
        """Copia un archivo local al destino (vía /tmp y luego mueve como root)."""
        if self.seco:
            self.log.info("[seco] copiar %s -> %s:%s", local, destino, ruta_remota)
            return
        carpeta = os.path.dirname(ruta_remota)
        if self._es_local(destino):
            r = subprocess.run(["sudo", "-n", "bash", "-c",
                                f"mkdir -p {shlex.quote(carpeta)} && cp {shlex.quote(local)} {shlex.quote(ruta_remota)}"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise ErrorPermanente(f"copia local fallida: {r.stderr}")
            return
        tmp = f"/tmp/orq-{os.getpid()}-{os.path.basename(ruta_remota)}"
        s = self.cfg["ssh"]
        scp = ["scp", "-q", "-i", s["llave"], "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
               "-o", "StrictHostKeyChecking=accept-new", local, f"{s['usuario']}@{self.ips[destino]}:{tmp}"]
        try:
            r = subprocess.run(scp, capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired as e:
            raise ErrorTransitorio(f"tiempo agotado copiando a {destino}") from e
        if r.returncode != 0:
            raise ErrorTransitorio(f"scp a {destino} falló: {r.stderr.strip()}")
        self.ejecutar(destino, f"mkdir -p {shlex.quote(carpeta)}\nmv {tmp} {shlex.quote(ruta_remota)}",
                      descripcion or "mover archivo copiado")
