"""Plantillas de topología · patrón Método Fábrica.

El procedimiento para generar un slice es común (crear nodos, crear enlaces, validar).
El paso que varía —qué pares de nodos se enlazan— lo resuelve cada generador concreto.
Agregar una topología nueva (malla, árbol, bus) es agregar una subclase, sin tocar el resto.
"""
from abc import ABC, abstractmethod

from .modelo import Nodo, Enlace, SliceSpec, ErrorValidacion


class GeneradorTopologia(ABC):
    minimo_nodos = 1

    def generar(self, nombre, n=None, flavor="cirros-small", nombres=None, acceso=()):
        """Procedimiento común. `nombres` fija los nombres y el orden de los nodos;
        `acceso` es la lista de nodos que tendrán red de acceso."""
        nombres = list(nombres) if nombres else [f"vm{i + 1}" for i in range(n)]
        if len(nombres) < self.minimo_nodos:
            raise ErrorValidacion(
                f"{type(self).__name__} requiere al menos {self.minimo_nodos} nodos")
        nodos = [Nodo(x, flavor, x in acceso) for x in nombres]
        enlaces = [Enlace(f"e{k + 1}", a, b) for k, (a, b) in enumerate(self.pares(nombres))]
        return SliceSpec(nombre, nodos, enlaces)

    @abstractmethod
    def pares(self, nombres):
        """Método fábrica: qué pares de nodos se enlazan."""


class Lineal(GeneradorTopologia):
    minimo_nodos = 2

    def pares(self, nombres):
        return list(zip(nombres, nombres[1:]))


class Anillo(GeneradorTopologia):
    minimo_nodos = 3

    def pares(self, nombres):
        return list(zip(nombres, nombres[1:])) + [(nombres[-1], nombres[0])]


_GENERADORES = {"lineal": Lineal, "anillo": Anillo}


def plantilla(tipo):
    """Devuelve el generador de la topología pedida."""
    try:
        return _GENERADORES[tipo]()
    except KeyError:
        raise ErrorValidacion(f"topología desconocida '{tipo}'. Disponibles: {list(_GENERADORES)}")


def componer(nombre, *specs, flavors=None):
    """Une varios slices en uno. Los nodos con el mismo nombre se fusionan (son el mismo nodo);
    los enlaces se renombran e1..eN y se descartan duplicados. `flavors` permite fijar el
    flavor de nodos concretos en el resultado."""
    flavors = flavors or {}
    nodos, enlaces, pares = {}, [], set()
    for s in specs:
        for n in s.nodos:
            if n.nombre in nodos:
                nodos[n.nombre].acceso = nodos[n.nombre].acceso or n.acceso
            else:
                nodos[n.nombre] = Nodo(n.nombre, n.flavor, n.acceso)
        for e in s.enlaces:
            par = frozenset((e.a, e.b))
            if par not in pares:
                pares.add(par)
                enlaces.append((e.a, e.b))
    for nom, fl in flavors.items():
        nodos[nom].flavor = fl
    return SliceSpec(nombre, list(nodos.values()),
                     [Enlace(f"e{k + 1}", a, b) for k, (a, b) in enumerate(enlaces)])


def ex1():
    """EX1-Topology: anillo VM1-VM4-VM3-VM2 + lineal VM4-VM5-VM6, compartiendo VM4.
    VM1 y VM3 con red de acceso (SSH y salida a Internet). VM2 y VM6 son cirros."""
    anillo = plantilla("anillo").generar("ex1-anillo", nombres=["VM1", "VM4", "VM3", "VM2"],
                                         flavor="ubuntu-small", acceso=["VM1", "VM3"])
    lineal = plantilla("lineal").generar("ex1-lineal", nombres=["VM4", "VM5", "VM6"],
                                         flavor="ubuntu-small")
    return componer("ex1", anillo, lineal,
                    flavors={"VM2": "cirros-small", "VM6": "cirros-small"})
