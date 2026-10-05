"""Modelo del slice (parte de K2 · Modelador).

Un slice es un grafo bipartito:
- nodos (VMs),
- segmentos (dominios de capa 2: uno por enlace y, si algún nodo lo pide, uno de acceso),
- incidencias (nodo conectado a segmento = una interfaz de red de la VM).
"""
from dataclasses import dataclass, field, asdict

SEGMENTO_ACCESO = "acc"


class ErrorValidacion(Exception):
    pass


@dataclass
class Nodo:
    nombre: str
    flavor: str
    acceso: bool = False      # red de acceso: SSH desde fuera + salida a Internet


@dataclass
class Enlace:
    nombre: str
    a: str
    b: str


@dataclass
class SliceSpec:
    nombre: str
    nodos: list = field(default_factory=list)
    enlaces: list = field(default_factory=list)

    def a_dict(self):
        return asdict(self)

    @staticmethod
    def desde_dict(d):
        return SliceSpec(d["nombre"],
                         [Nodo(**n) for n in d["nodos"]],
                         [Enlace(**e) for e in d["enlaces"]])

    def nodo(self, nombre):
        return next(n for n in self.nodos if n.nombre == nombre)


def validar(spec, cfg, capacidades=None):
    """Validación estructural y contra las capacidades declaradas por la familia (rechazo temprano)."""
    errores = []
    nombres = [n.nombre for n in spec.nodos]
    if not spec.nodos:
        errores.append("el slice no tiene nodos")
    if len(nombres) != len(set(nombres)):
        errores.append("hay nombres de nodo repetidos")
    for n in spec.nodos:
        if n.flavor not in cfg["flavors"]:
            errores.append(f"flavor desconocido '{n.flavor}' en {n.nombre}")
        elif capacidades is not None:
            img = cfg["flavors"][n.flavor]["imagen"]
            if img not in capacidades.get("imagenes", []):
                errores.append(f"la plataforma no ofrece la imagen '{img}' ({n.nombre})")
    pares = set()
    for e in spec.enlaces:
        if e.a not in nombres or e.b not in nombres:
            errores.append(f"el enlace {e.nombre} referencia un nodo inexistente")
        if e.a == e.b:
            errores.append(f"el enlace {e.nombre} une un nodo consigo mismo")
        par = frozenset((e.a, e.b))
        if par in pares:
            errores.append(f"enlace duplicado entre {e.a} y {e.b}")
        pares.add(par)
    if len({e.nombre for e in spec.enlaces}) != len(spec.enlaces):
        errores.append("hay nombres de enlace repetidos")
    if spec.nodos and not _conexo(spec):
        errores.append("la topología no es conexa")
    if errores:
        raise ErrorValidacion("; ".join(errores))


def _conexo(spec):
    ady = {n.nombre: set() for n in spec.nodos}
    for e in spec.enlaces:
        if e.a in ady and e.b in ady:
            ady[e.a].add(e.b)
            ady[e.b].add(e.a)
    inicio = spec.nodos[0].nombre
    vistos, pila = {inicio}, [inicio]
    while pila:
        for v in ady[pila.pop()]:
            if v not in vistos:
                vistos.add(v)
                pila.append(v)
    return len(vistos) == len(ady)


def modelar(spec):
    """Construye el grafo bipartito. El orden de las interfaces de cada nodo es estable:
    primero la de acceso (eth0, para que cualquier imagen la configure por DHCP) y luego
    las de enlace en el orden de los enlaces."""
    segmentos = {e.nombre: {"tipo": "enlace", "extremos": [e.a, e.b]} for e in spec.enlaces}
    con_acceso = [n.nombre for n in spec.nodos if n.acceso]
    if con_acceso:
        segmentos[SEGMENTO_ACCESO] = {"tipo": "acceso", "extremos": con_acceso}
    incidencias = {}
    for n in spec.nodos:
        lista = []
        if n.acceso:
            lista.append(SEGMENTO_ACCESO)
        lista += [e.nombre for e in spec.enlaces if n.nombre in (e.a, e.b)]
        incidencias[n.nombre] = lista
    return {
        "nodos": {n.nombre: {"flavor": n.flavor, "acceso": n.acceso} for n in spec.nodos},
        "orden_nodos": [n.nombre for n in spec.nodos],
        "segmentos": segmentos,
        "orden_enlaces": [e.nombre for e in spec.enlaces],
        "incidencias": incidencias,
    }
