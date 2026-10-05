"""K3 · Gestor de Capacidad y Recursos Únicos.

- Colocación: decide en qué servidor va cada VM (estrategia "dispersar": el servidor con más
  vCPU libre, con desempate por nombre para que la demo sea repetible). Los nodos ya colocados
  no se mueven.
- Pools: reserva identificadores que no pueden repetirse entre slices (VLAN, MAC, consola,
  puerto SSH externo, subred de acceso). Todas las reservas de un slice se hacen en una sola
  transacción sobre los pools, de modo que dos despliegues concurrentes nunca reciben lo mismo.
"""
import ipaddress

from .inventario import uso_por_servidor
from .modelo import SEGMENTO_ACCESO


class SinCapacidad(Exception):
    pass


def colocar(cfg, estado, sid, modelo, colocacion_previa):
    uso = uso_por_servidor(cfg, estado, excluir_slice=sid)
    sob = cfg["sobreasignacion"]
    libre = {s["nombre"]: {"vcpu": s["vcpu"] * sob["vcpu"] - uso[s["nombre"]]["vcpu"],
                           "ram_mb": s["ram_mb"] * sob["ram"] - uso[s["nombre"]]["ram_mb"]}
             for s in cfg["servidores"]}
    colocacion = {}
    for nodo in modelo["orden_nodos"]:
        fl = cfg["flavors"][modelo["nodos"][nodo]["flavor"]]
        if nodo in colocacion_previa:
            srv = colocacion_previa[nodo]
        else:
            candidatos = [n for n, l in libre.items() if l["vcpu"] >= fl["vcpu"] and l["ram_mb"] >= fl["ram_mb"]]
            if not candidatos:
                raise SinCapacidad(f"no hay servidor con capacidad para {nodo} ({fl})")
            srv = sorted(candidatos, key=lambda n: (-libre[n]["vcpu"], -libre[n]["ram_mb"], n))[0]
        libre[srv]["vcpu"] -= fl["vcpu"]
        libre[srv]["ram_mb"] -= fl["ram_mb"]
        colocacion[nodo] = srv
    return colocacion


def _tomar(pool, candidatos, sid, clave_previa=None):
    """Devuelve un valor libre del pool y lo marca como del slice. Reutiliza el previo si sigue siendo suyo."""
    if clave_previa is not None and pool.get(str(clave_previa)) == sid:
        return clave_previa
    for c in candidatos:
        if str(c) not in pool:
            pool[str(c)] = sid
            return c
    raise SinCapacidad("pool agotado")


def reservar(cfg, estado, sid, modelo, previas):
    """Calcula todas las asignaciones del slice. `previas` son las del despliegue anterior (se conservan)."""
    pc = cfg["pools"]
    previas = previas or {}
    a = {"vlans": {}, "macs": {}, "consolas": {}, "ssh": {}, "acceso": None, "ips_enlace": {}}
    with estado.transaccion_pools() as pools:
        vlans = range(pc["vlan"][0], pc["vlan"][1] + 1)
        for seg in modelo["segmentos"]:
            a["vlans"][seg] = _tomar(pools["vlan"], vlans, sid, previas.get("vlans", {}).get(seg))
        pref = pc["prefijo_mac"]
        macs = (f"{pref}:{i >> 8:02x}:{i & 0xff:02x}" for i in range(1, 0xffff))
        for nodo, segs in modelo["incidencias"].items():
            for seg in segs:
                k = f"{nodo}|{seg}"
                a["macs"][k] = _tomar(pools["mac"], macs, sid, previas.get("macs", {}).get(k))
        consolas = range(pc["vnc_display"][0], pc["vnc_display"][1] + 1)
        for nodo in modelo["orden_nodos"]:
            a["consolas"][nodo] = _tomar(pools["vnc"], consolas, sid, previas.get("consolas", {}).get(nodo))
        if SEGMENTO_ACCESO in modelo["segmentos"]:
            subredes = list(ipaddress.ip_network(pc["red_acceso"]).subnets(new_prefix=pc["prefijo_acceso"]))
            idx_prev = (previas.get("acceso") or {}).get("indice")
            idx = _tomar(pools["acceso"], range(len(subredes)), sid, idx_prev)
            red = subredes[idx]
            hosts = list(red.hosts())
            ips = {}
            for k, nodo in enumerate(modelo["segmentos"][SEGMENTO_ACCESO]["extremos"]):
                ips[nodo] = str(hosts[k + 1])          # hosts[0] es la puerta de enlace
            a["acceso"] = {"indice": idx, "subred": str(red), "puerta": str(hosts[0]), "ips": ips}
            puertos = range(pc["puertos_ssh"][0], pc["puertos_ssh"][1] + 1)
            for nodo in ips:
                a["ssh"][nodo] = _tomar(pools["ssh"], puertos, sid, previas.get("ssh", {}).get(nodo))
    # Direcciones de los enlaces: internas al slice (aisladas por VLAN), no requieren pool global.
    base = ipaddress.ip_network(cfg["pools"]["red_enlaces"])
    subredes24 = base.subnets(new_prefix=24)
    next(subredes24)                                    # se omite la primera
    for k, (enl, red) in enumerate(zip(modelo["orden_enlaces"], subredes24)):
        a_, b_ = modelo["segmentos"][enl]["extremos"]
        hosts = list(red.hosts())
        a["ips_enlace"][enl] = {a_: f"{hosts[0]}/24", b_: f"{hosts[1]}/24"}
    return a


def liberar(estado, sid):
    """Devuelve a los pools todo lo que el slice tenía reservado."""
    with estado.transaccion_pools() as pools:
        for nombre in pools:
            for k in [k for k, v in pools[nombre].items() if v == sid]:
                del pools[nombre][k]


def sincronizar(estado, sid, a):
    """Tras un despliegue exitoso, libera lo que el slice ya no usa (por ejemplo, tras quitar un enlace)."""
    usados = {"vlan": {str(v) for v in a["vlans"].values()}, "mac": set(a["macs"].values()),
              "vnc": {str(v) for v in a["consolas"].values()}, "ssh": {str(v) for v in a["ssh"].values()},
              "acceso": {str(a["acceso"]["indice"])} if a["acceso"] else set()}
    with estado.transaccion_pools() as pools:
        for nombre, en_uso in usados.items():
            for k in [k for k, v in pools[nombre].items() if v == sid and k not in en_uso]:
                del pools[nombre][k]
