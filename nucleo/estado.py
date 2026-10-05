"""Persistencia del estado del orquestador en archivos JSON.

- <estado>/slices/<id>.json : especificación, colocación, asignaciones, recursos creados y bitácora.
- <estado>/pools.json       : identificadores únicos ocupados (VLAN, MAC, VNC, SSH, subredes de acceso).

Escrituras atómicas (archivo temporal + reemplazo) y bloqueo entre hilos y entre procesos
para los pools, que es el dato que comparten todos los despliegues.
"""
import contextlib
import copy
import fcntl
import json
import os
import threading


class Estado:
    def __init__(self, directorio):
        self.dir = directorio
        self.dir_slices = os.path.join(directorio, "slices")
        os.makedirs(self.dir_slices, exist_ok=True)
        self._ruta_pools = os.path.join(directorio, "pools.json")
        self._ruta_bloqueo = os.path.join(directorio, ".bloqueo")
        self._hilos = threading.RLock()
        self._escritura = threading.Lock()

    # ---------- utilidades
    @staticmethod
    def _escribir(ruta, datos):
        tmp = ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=2, ensure_ascii=False)
        os.replace(tmp, ruta)

    @staticmethod
    def _leer(ruta, defecto):
        if not os.path.exists(ruta):
            return copy.deepcopy(defecto)
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)

    # ---------- slices
    def ruta_slice(self, sid):
        return os.path.join(self.dir_slices, f"{sid}.json")

    def leer_slice(self, sid):
        return self._leer(self.ruta_slice(sid), None)

    def guardar_slice(self, datos):
        with self._escritura:
            self._escribir(self.ruta_slice(datos["id"]), datos)

    def listar_slices(self):
        res = []
        for nombre in sorted(os.listdir(self.dir_slices)):
            if nombre.endswith(".json"):
                res.append(self._leer(os.path.join(self.dir_slices, nombre), None))
        return [s for s in res if s]

    # ---------- pools
    @contextlib.contextmanager
    def transaccion_pools(self):
        """Lee, permite modificar y guarda los pools como una sola operación exclusiva."""
        vacio = {"vlan": {}, "mac": {}, "vnc": {}, "ssh": {}, "acceso": {}}
        with self._hilos:
            with open(self._ruta_bloqueo, "w") as fb:
                fcntl.flock(fb, fcntl.LOCK_EX)
                try:
                    pools = self._leer(self._ruta_pools, vacio)
                    for k, v in vacio.items():
                        pools.setdefault(k, v)
                    yield pools
                    self._escribir(self._ruta_pools, pools)
                finally:
                    fcntl.flock(fb, fcntl.LOCK_UN)
