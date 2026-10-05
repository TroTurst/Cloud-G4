"""K1 · Conductor: recorre las etapas de un trabajo en orden. Es la entrada del sistema en esta
entrega ("sin APIs, solo funciones").

Funciones públicas:
    desplegar(spec, zona="linux", slice_id=None) -> dict
    eliminar(slice_id) -> dict
    estado_slice(slice_id) -> dict
    listar() -> list
    plan_en_seco(spec, zona="linux") -> list[str]

Etapas de desplegar: 0 tomar · 1 validar · 2 modelar · 3 refrescar · 4-5 colocar y reservar ·
6 planificar · 7 confirmar · 8 ejecutar · 9 terminar.
"""
import threading
import time
import uuid

from . import capacidad, modelo as mod, planificador
from .catalogo import catalogo_por_defecto
from .config import cargar_config, configurar_logs, log_slice
from .ejecucion import MotorEjecucion
from .estado import Estado

_ctx = None
_ctx_bloqueo = threading.Lock()
_bloqueos_slice = {}


class _Contexto:
    def __init__(self, cfg):
        self.cfg = cfg
        configurar_logs(cfg)
        self.log = log_slice("-")
        self.estado = Estado(cfg["directorio_estado"])
        self.catalogo = catalogo_por_defecto(cfg, self.log)
        self.motor = MotorEjecucion(cfg, self.estado)


def iniciar(cfg=None, **sobrescribir):
    """Inicializa el orquestador (se llama solo la primera vez). Permite pasar otra configuración."""
    global _ctx
    with _ctx_bloqueo:
        if _ctx is None or cfg is not None or sobrescribir:
            _ctx = _Contexto(cfg or cargar_config(**sobrescribir))
    return _ctx


def _c():
    return _ctx or iniciar()


def _bloqueo_de(sid):
    """Un solo trabajo activo por slice."""
    with _ctx_bloqueo:
        return _bloqueos_slice.setdefault(sid, threading.Lock())


def _ahora():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _salidas(sl):
    sal = {}
    for nodo, srv in sl["colocacion"].items():
        s = {"servidor": srv}
        con = sl["recursos"].get(f"con:{nodo}")
        if con:
            s["consola"] = con["real"]
        acc = sl["recursos"].get(f"acc:{nodo}")
        if acc:
            s["ssh"] = acc["real"].get("ssh")
            s["ip_acceso"] = acc["real"].get("ip_interna")
        sal[nodo] = s
    return sal


# ====================================================================== desplegar
def desplegar(spec, zona="linux", slice_id=None):
    """Crea un slice nuevo o, si se pasa slice_id, lleva un slice existente a la nueva especificación."""
    c = _c()
    sid = slice_id or uuid.uuid4().hex[:6]
    log = log_slice(sid)
    with _bloqueo_de(sid):
        # 0 · tomar
        sl = c.estado.leer_slice(sid) if slice_id else None
        nuevo = sl is None
        if nuevo:
            sl = {"id": sid, "nombre": spec.nombre, "zona": zona, "creado": _ahora(), "recursos": {},
                  "bitacora": [], "colocacion": {}, "asignaciones": {}, "flavores": {}}
        zona = sl["zona"]
        familia = c.catalogo.familia(zona)
        log.info("desplegar '%s' en zona '%s' (%d nodos, %d enlaces)", spec.nombre, zona,
                 len(spec.nodos), len(spec.enlaces))
        try:
            # 1 · validar (estructura + capacidades declaradas por la familia)
            mod.validar(spec, c.cfg, familia.capacidades())
            # 2 · modelar
            modelo = mod.modelar(spec)
            sl.update(spec=spec.a_dict(), estado="Desplegando", actualizado=_ahora(), error=None)
            # 3 · refrescar (en un slice nuevo, REAL = vacío)
            if not nuevo:
                c.motor.refrescar(sl, familia, log)
            # 4-5 · colocar y reservar
            colocacion = capacidad.colocar(c.cfg, c.estado, sid,
                                           modelo, {k: v for k, v in sl["colocacion"].items() if k in modelo["nodos"]})
            asign = capacidad.reservar(c.cfg, c.estado, sid, modelo, sl.get("asignaciones"))
            sl["colocacion"], sl["asignaciones"] = colocacion, asign
            sl["flavores"] = {n: v["flavor"] for n, v in modelo["nodos"].items()}
            c.estado.guardar_slice(sl)
            log.info("colocación: %s", colocacion)
            # 6 · planificar
            deseados = planificador.recursos_deseados(c.cfg, sid, modelo, colocacion, asign)
            plan = planificador.planificar(deseados, sl["recursos"])
            log.info("plan: %d acciones en %d olas", plan.total, len(plan.olas))
            for linea in plan.resumen():
                log.debug(linea)
            # 7 · confirmar: en esta versión las reservas quedan confirmadas al reservar.
        except (mod.ErrorValidacion, capacidad.SinCapacidad, KeyError) as e:
            log.error("rechazado: %s", e)
            if nuevo:
                capacidad.liberar(c.estado, sid)
            sl.update(estado="Rechazado" if nuevo else "Activo", error=str(e), actualizado=_ahora())
            c.estado.guardar_slice(sl)
            return {"id": sid, "estado": sl["estado"], "error": str(e)}

        # 8 · ejecutar
        t0 = time.time()
        ok, error = c.motor.ejecutar(sl, plan, familia, log, compensar=True)
        # 9 · terminar
        if ok:
            capacidad.sincronizar(c.estado, sid, sl["asignaciones"])
            sl["estado"] = "Activo"
            sl["salidas"] = _salidas(sl)
            log.info("slice activo en %.1f s", time.time() - t0)
        else:
            if nuevo and not sl["recursos"]:
                capacidad.liberar(c.estado, sid)          # nada quedó creado: se devuelve todo
                sl["estado"] = "Fallido"
            elif nuevo:
                sl["estado"] = "Error"                    # quedaron recursos: se conservan sus reservas
            else:
                sl["estado"] = "Error"
            sl["error"] = error
            log.error("despliegue fallido (%s): %s", sl["estado"], error)
        sl["actualizado"] = _ahora()
        c.estado.guardar_slice(sl)
        return {"id": sid, "estado": sl["estado"], "error": sl.get("error"),
                "acciones": plan.total, "olas": len(plan.olas), "salidas": sl.get("salidas", {})}


# ====================================================================== eliminar
def eliminar(slice_id):
    """DESEADO = vacío: todo sale destruir, en orden inverso. No se compensa: se reintenta,
    y lo que no se pueda borrar queda con su marca de propiedad para un barrido posterior."""
    c = _c()
    log = log_slice(slice_id)
    with _bloqueo_de(slice_id):
        sl = c.estado.leer_slice(slice_id)
        if sl is None:
            return {"id": slice_id, "estado": "Inexistente"}
        familia = c.catalogo.familia(sl["zona"])
        sl.update(estado="Eliminando", actualizado=_ahora())
        c.estado.guardar_slice(sl)
        log.info("eliminar slice '%s' (%d recursos registrados)", sl["nombre"], len(sl["recursos"]))
        try:
            c.motor.refrescar(sl, familia, log)
        except Exception as e:                         # si no se puede leer, se intenta destruir igual
            log.warning("no se pudo refrescar: %s", e)
        plan = planificador.planificar({}, sl["recursos"])
        ok, error = c.motor.ejecutar(sl, plan, familia, log, compensar=False)
        if ok:
            capacidad.liberar(c.estado, slice_id)
            sl.update(estado="Eliminado", salidas={}, error=None)
            log.info("slice eliminado")
        else:
            sl.update(estado="Error", error=error)
            log.error("eliminación incompleta: %s", error)
        sl["actualizado"] = _ahora()
        c.estado.guardar_slice(sl)
        return {"id": slice_id, "estado": sl["estado"], "error": sl.get("error"), "acciones": plan.total}


# ====================================================================== consultas
def estado_slice(slice_id):
    sl = _c().estado.leer_slice(slice_id)
    if sl is None:
        return {"id": slice_id, "estado": "Inexistente"}
    return {"id": sl["id"], "nombre": sl["nombre"], "zona": sl["zona"], "estado": sl["estado"],
            "colocacion": sl["colocacion"], "recursos": len(sl["recursos"]),
            "salidas": sl.get("salidas", {}), "error": sl.get("error")}


def listar():
    return [{"id": s["id"], "nombre": s["nombre"], "zona": s["zona"], "estado": s.get("estado"),
             "nodos": len(s.get("colocacion", {}))} for s in _c().estado.listar_slices()]


def plan_en_seco(spec, zona="linux"):
    """Muestra el plan que se ejecutaría para un slice nuevo, sin reservar ni ejecutar nada."""
    c = _c()
    familia = c.catalogo.familia(zona)
    mod.validar(spec, c.cfg, familia.capacidades())
    modelo = mod.modelar(spec)
    colocacion = capacidad.colocar(c.cfg, c.estado, "__seco__", modelo, {})
    falsos = _AsignacionesFicticias(c.cfg, modelo)
    deseados = planificador.recursos_deseados(c.cfg, "seco00", modelo, colocacion, falsos.a)
    plan = planificador.planificar(deseados, {})
    return [f"colocación: {colocacion}"] + plan.resumen()


class _AsignacionesFicticias:
    """Asignaciones de ejemplo para plan_en_seco (no tocan los pools)."""
    def __init__(self, cfg, modelo):
        import ipaddress
        pc = cfg["pools"]
        vl = iter(range(pc["vlan"][0], pc["vlan"][1] + 1))
        a = {"vlans": {s: next(vl) for s in modelo["segmentos"]}, "macs": {}, "consolas": {}, "ssh": {},
             "acceso": None, "ips_enlace": {}}
        k = 1
        for n, segs in modelo["incidencias"].items():
            for s in segs:
                a["macs"][f"{n}|{s}"] = f"{pc['prefijo_mac']}:ff:{k:02x}"
                k += 1
        for i, n in enumerate(modelo["orden_nodos"]):
            a["consolas"][n] = pc["vnc_display"][0] + i
        if mod.SEGMENTO_ACCESO in modelo["segmentos"]:
            red = next(ipaddress.ip_network(pc["red_acceso"]).subnets(new_prefix=pc["prefijo_acceso"]))
            h = list(red.hosts())
            ext = modelo["segmentos"][mod.SEGMENTO_ACCESO]["extremos"]
            a["acceso"] = {"indice": 0, "subred": str(red), "puerta": str(h[0]),
                           "ips": {n: str(h[i + 1]) for i, n in enumerate(ext)}}
            a["ssh"] = {n: pc["puertos_ssh"][0] + i for i, n in enumerate(ext)}
        for j, e in enumerate(modelo["orden_enlaces"], 1):
            x, y = modelo["segmentos"][e]["extremos"]
            a["ips_enlace"][e] = {x: f"192.168.{j}.1/24", y: f"192.168.{j}.2/24"}
        self.a = a
