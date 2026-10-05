"""Familia Simulada · fábrica concreta en memoria.

Permite probar el núcleo (plan, olas, compensación) sin tocar servidores, y demostrar que
agregar una plataforma no modifica el núcleo. Admite inyección de fallas:
cfg["simulada"]["fallar_en"] = ["cmp:VM3"]  -> la creación de ese recurso falla (error permanente).
"""
import random
import threading
import time

from .contrato import FamiliaProveedores, Proveedor, ErrorPermanente, TIPOS

_RECURSOS = {}
_BLOQUEO = threading.Lock()


def recursos_simulados():
    with _BLOQUEO:
        return dict(_RECURSOS)


class ProductoSimulado(Proveedor):
    def __init__(self, tipo, cfg, log):
        self.tipo = tipo
        self.cfg = cfg
        self.log = log

    def _clave(self, rec):
        return f"{rec['params'].get('slice')}/{rec['id']}"

    def crear(self, rec):
        time.sleep(random.uniform(0.01, 0.05))
        if rec["id"] in self.cfg.get("simulada", {}).get("fallar_en", []):
            raise ErrorPermanente(f"falla simulada al crear {rec['id']}")
        with _BLOQUEO:
            _RECURSOS[self._clave(rec)] = {"servidor": rec["servidor"], **rec["params"]}
        return {"simulado": True, "servidor": rec["servidor"]}

    def leer(self, rec, real):
        with _BLOQUEO:
            return real if self._clave(rec) in _RECURSOS else None

    def destruir(self, rec, real):
        with _BLOQUEO:
            _RECURSOS.pop(self._clave(rec), None)


class FabricaSimulada(FamiliaProveedores):
    nombre = "simulada"

    def _p(self, tipo):
        return ProductoSimulado(tipo, self.cfg, self.log)

    def crear_conmutacion(self): return self._p("conmutacion")
    def crear_red(self): return self._p("red")
    def crear_volumen(self): return self._p("volumen")
    def crear_puerto(self): return self._p("puerto")
    def crear_computo(self): return self._p("computo")
    def crear_acceso(self): return self._p("acceso")
    def crear_consola(self): return self._p("consola")

    def capacidades(self):
        return {"imagenes": list(self.cfg["imagenes"]["catalogo"]), "tipos": TIPOS,
                "aislamiento": "simulado", "servidores": [s["nombre"] for s in self.cfg["servidores"]]}
