"""K4 · Motor de Ejecución: única puerta de comandos hacia los proveedores.

- Refrescar: pregunta a los proveedores si lo registrado sigue existiendo (REAL).
- Ejecutar: recorre el plan ola por ola; dentro de cada ola, en paralelo, con un límite global
  y un límite por servidor. Cada recurso creado se registra (con su resultado real) ANTES de
  continuar, y cada acción queda en la bitácora.
- Compensar: si una acción falla de forma permanente, recorre lo creado en este trabajo en orden
  inverso y lo destruye. El slice nunca queda a medias.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from proveedores.contrato import con_reintentos, ErrorProveedor


class MotorEjecucion:
    def __init__(self, cfg, estado):
        self.cfg = cfg
        self.estado = estado
        self._bloqueo = threading.Lock()
        self._sem_servidor = {}

    def _semaforo(self, servidor):
        with self._bloqueo:
            if servidor not in self._sem_servidor:
                self._sem_servidor[servidor] = threading.Semaphore(
                    self.cfg["ejecucion"]["paralelismo_por_servidor"])
            return self._sem_servidor[servidor]

    def _bitacora(self, sl, op, rid, resultado, detalle=""):
        sl["bitacora"].append({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "op": op, "recurso": rid,
                               "resultado": resultado, "detalle": detalle})

    # ------------------------------------------------------------------ refrescar
    def refrescar(self, sl, familia, log):
        """Quita del registro los recursos que ya no existen en la infraestructura."""
        e = self.cfg["ejecucion"]
        for rid, v in list(sl["recursos"].items()):
            prod = familia.producto(v["recurso"]["tipo"])
            real = con_reintentos(prod.leer, v["recurso"], v["real"], intentos=e["reintentos"],
                                  espera=e["espera_reintento_s"], log=log, descripcion=f"leer {rid}")
            if real is None:
                log.warning("desvío: %s ya no existe en la infraestructura", rid)
                del sl["recursos"][rid]
        self.estado.guardar_slice(sl)

    # ------------------------------------------------------------------ ejecutar
    def _accion(self, sl, familia, accion, log, creados):
        rec = accion.recurso
        prod = familia.producto(rec["tipo"])
        e = self.cfg["ejecucion"]
        with self._semaforo(rec["servidor"]):
            if accion.op == "crear":
                prod.validar(rec)
                real = con_reintentos(prod.crear, rec, intentos=e["reintentos"],
                                      espera=e["espera_reintento_s"], log=log, descripcion=f"crear {rec['id']}")
                with self._bloqueo:
                    sl["recursos"][rec["id"]] = {"recurso": rec, "real": real}
                    creados.append(rec["id"])
                    self._bitacora(sl, "crear", rec["id"], "ok")
                    self.estado.guardar_slice(sl)
            else:
                real = sl["recursos"].get(rec["id"], {}).get("real")
                con_reintentos(prod.destruir, rec, real, intentos=e["reintentos"],
                               espera=e["espera_reintento_s"], log=log, descripcion=f"destruir {rec['id']}")
                with self._bloqueo:
                    sl["recursos"].pop(rec["id"], None)
                    self._bitacora(sl, "destruir", rec["id"], "ok")
                    self.estado.guardar_slice(sl)
        log.info("%s %s @ %s: ok", accion.op, rec["id"], rec["servidor"])

    def ejecutar(self, sl, plan, familia, log, compensar=True):
        """Devuelve (True, None) si todo salió bien; (False, mensaje) si no."""
        creados = []
        for n, ola in enumerate(plan.olas, 1):
            log.info("ola %d/%d: %d acciones", n, len(plan.olas), len(ola))
            errores = []
            with ThreadPoolExecutor(max_workers=self.cfg["ejecucion"]["paralelismo_global"]) as pool:
                futuros = {pool.submit(self._accion, sl, familia, a, log, creados): a for a in ola}
                for f, a in futuros.items():
                    try:
                        f.result()
                    except (ErrorProveedor, KeyError, OSError) as ex:
                        errores.append(f"{a.op} {a.recurso['id']}: {ex}")
                        with self._bloqueo:
                            self._bitacora(sl, a.op, a.recurso["id"], "error", str(ex))
                        log.error("%s %s falló: %s", a.op, a.recurso["id"], ex)
            self.estado.guardar_slice(sl)
            if errores:
                msg = " | ".join(errores)
                if compensar and creados:
                    self.compensar(sl, familia, creados, log)
                return False, msg
        return True, None

    def compensar(self, sl, familia, creados, log):
        """Destruye en orden inverso lo creado en este trabajo."""
        log.warning("compensando: se destruyen %d recursos creados en este trabajo", len(creados))
        e = self.cfg["ejecucion"]
        for rid in reversed(creados):
            v = sl["recursos"].get(rid)
            if not v:
                continue
            prod = familia.producto(v["recurso"]["tipo"])
            try:
                con_reintentos(prod.destruir, v["recurso"], v["real"], intentos=e["reintentos"],
                               espera=e["espera_reintento_s"], log=log, descripcion=f"compensar {rid}")
                del sl["recursos"][rid]
                self._bitacora(sl, "compensar", rid, "ok")
            except ErrorProveedor as ex:
                self._bitacora(sl, "compensar", rid, "error", str(ex))
                log.error("no se pudo compensar %s: %s (queda con su marca de propiedad)", rid, ex)
            self.estado.guardar_slice(sl)
