"""K2 · Modelador y Planificador.

1. Deriva los recursos concretos que el slice necesita (estado DESEADO), a partir del modelo,
   la colocación y las asignaciones. Los recursos son genéricos: no mencionan ninguna tecnología.
2. Resta DESEADO - REAL (lo registrado tras el refresco), recurso por recurso: crear o destruir.
3. Ordena las acciones en OLAS según el orden canónico. Dentro de una ola todo puede ir en paralelo.

Crear, editar y eliminar usan el mismo mecanismo: eliminar es simplemente DESEADO = vacío.
"""
from dataclasses import dataclass, field

from .modelo import SEGMENTO_ACCESO

ORDEN = ["conmutacion", "red", "volumen", "puerto", "computo", "acceso", "consola"]


def recursos_deseados(cfg, sid, modelo, colocacion, asign):
    rec = {}

    def agregar(id_, tipo, servidor, **params):
        rec[id_] = {"id": id_, "tipo": tipo, "servidor": servidor, "params": {"slice": sid, **params}}

    # Segmentos: conmutación (switch físico) + red
    for seg, info in modelo["segmentos"].items():
        servidores = sorted({colocacion[n] for n in info["extremos"]})
        es_acceso = info["tipo"] == "acceso"
        agregar(f"conm:{seg}", "conmutacion", "ofs", segmento=seg, vlan=asign["vlans"][seg],
                servidores=servidores, incluye_salida=es_acceso)
        params = {"segmento": seg, "vlan": asign["vlans"][seg], "acceso": es_acceso}
        if es_acceso:
            acc = asign["acceso"]
            params.update(subred=acc["subred"], puerta=acc["puerta"],
                          hosts=[{"mac": asign["macs"][f"{n}|{SEGMENTO_ACCESO}"], "ip": ip}
                                 for n, ip in acc["ips"].items()])
        agregar(f"red:{seg}", "red", "gateway" if es_acceso else "-", **params)

    # Nodos: volumen, puertos, cómputo, acceso externo, consola
    for idx, nodo in enumerate(modelo["orden_nodos"]):
        info = modelo["nodos"][nodo]
        fl = cfg["flavors"][info["flavor"]]
        srv = colocacion[nodo]
        vm = f"{sid}-{nodo}"
        interfaces = []
        for k, seg in enumerate(modelo["incidencias"][nodo]):
            mac = asign["macs"][f"{nodo}|{seg}"]
            es_acc = seg == SEGMENTO_ACCESO
            interfaces.append({"indice": k, "mac": mac, "segmento": seg, "dhcp": es_acc,
                               "direccion": None if es_acc else asign["ips_enlace"][seg][nodo]})
            agregar(f"pto:{nodo}:{k}", "puerto", srv, vm=vm, nodo=nodo, nodo_indice=idx, indice=k,
                    segmento=seg, vlan=asign["vlans"][seg], mac=mac)
        comunes = dict(vm=vm, nodo=nodo, nodo_indice=idx, imagen=fl["imagen"])
        agregar(f"vol:{nodo}", "volumen", srv, disco_gb=fl["disco_gb"], interfaces=interfaces, **comunes)
        agregar(f"cmp:{nodo}", "computo", srv, vcpu=fl["vcpu"], ram_mb=fl["ram_mb"],
                consola=asign["consolas"][nodo],
                interfaces=[{"indice": i["indice"], "mac": i["mac"]} for i in interfaces], **comunes)
        if info["acceso"]:
            agregar(f"acc:{nodo}", "acceso", "gateway", ip=asign["acceso"]["ips"][nodo],
                    puerto_externo=asign["ssh"][nodo], vlan=asign["vlans"][SEGMENTO_ACCESO], **comunes)
        agregar(f"con:{nodo}", "consola", srv, consola=asign["consolas"][nodo], **comunes)
    return rec


@dataclass
class Accion:
    op: str            # "crear" | "destruir"
    recurso: dict


@dataclass
class Plan:
    olas: list = field(default_factory=list)     # lista de listas de Accion

    @property
    def total(self):
        return sum(len(o) for o in self.olas)

    def resumen(self):
        return [f"ola {i + 1}: " + ", ".join(f"{a.op} {a.recurso['id']}" for a in ola)
                for i, ola in enumerate(self.olas)]


def planificar(deseados, registrados):
    """Resta DESEADO - REAL. `registrados`: {id: {"recurso": ..., "real": ...}}.
    Fase A: creaciones en orden canónico. Fase B: destrucciones en orden inverso (al final)."""
    crear = [r for i, r in deseados.items() if i not in registrados]
    destruir = [v["recurso"] for i, v in registrados.items() if i not in deseados]
    # Un recurso registrado cuyo deseado cambió (p. ej. otra VLAN) se reemplaza: destruir + crear.
    for i, r in deseados.items():
        if i in registrados and registrados[i]["recurso"]["params"] != r["params"]:
            destruir.append(registrados[i]["recurso"])
            crear.append(r)
    olas = []
    reemplazos = {r["id"] for r in crear} & {r["id"] for r in destruir}
    # Los reemplazos se destruyen primero (en orden inverso) para liberar nombres y puertos.
    for tipo in reversed(ORDEN):
        ola = [Accion("destruir", r) for r in destruir if r["tipo"] == tipo and r["id"] in reemplazos]
        if ola:
            olas.append(ola)
    for tipo in ORDEN:
        ola = [Accion("crear", r) for r in crear if r["tipo"] == tipo]
        if ola:
            olas.append(ola)
    for tipo in reversed(ORDEN):
        ola = [Accion("destruir", r) for r in destruir if r["tipo"] == tipo and r["id"] not in reemplazos]
        if ola:
            olas.append(ola)
    return Plan(olas)
