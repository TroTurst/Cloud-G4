"""Contrato de proveedores (el "driver / SDK" del enunciado) y su nivel común.

El núcleo SOLO conoce este archivo. Cada plataforma (Linux, Simulada, más adelante OpenStack)
implementa una FamiliaProveedores, que es una Fábrica Abstracta: construye todos los productos
de una misma plataforma, de modo que nunca se mezclen recursos de plataformas distintas.

Formato de un recurso (diccionario serializable):
    {"id": "cmp:VM1", "tipo": "computo", "servidor": "server1", "params": {...}}
"""
import time
from abc import ABC, abstractmethod

TIPOS = ["conmutacion", "red", "volumen", "puerto", "computo", "acceso", "consola"]


# ---------------------------------------------------------------- errores
class ErrorProveedor(Exception):
    """Base de los errores que devuelve un proveedor."""


class ErrorTransitorio(ErrorProveedor):
    """Puede resolverse reintentando (tiempo agotado, conexión caída)."""


class ErrorPermanente(ErrorProveedor):
    """No se resuelve reintentando (imagen inexistente, comando inválido)."""


class RequiereReemplazo(ErrorProveedor):
    """El cambio toca un atributo inmutable: hay que destruir y volver a crear."""


# ---------------------------------------------------------------- nivel común
def con_reintentos(funcion, *args, intentos=3, espera=2.0, log=None, descripcion=""):
    """Reintenta errores transitorios con espera creciente. Los permanentes se propagan."""
    for intento in range(1, intentos + 1):
        try:
            return funcion(*args)
        except ErrorTransitorio as e:
            if intento == intentos:
                raise ErrorPermanente(f"{descripcion}: agotados {intentos} intentos ({e})") from e
            if log:
                log.warning("transitorio en %s (intento %d/%d): %s", descripcion, intento, intentos, e)
            time.sleep(espera * intento)


def marca(slice_id, elemento):
    """Marca de propiedad: vincula un recurso real con su slice y su elemento lógico.
    Permite adoptar en vez de duplicar y limpiar huérfanos."""
    return f"orq-{slice_id}-{elemento}".replace(":", "-")


# ---------------------------------------------------------------- contrato
class Proveedor(ABC):
    """Contrato que implementa cada producto de cada familia."""
    tipo = None
    atributos_inmutables = ()

    def esquema(self):
        return {"tipo": self.tipo, "inmutables": list(self.atributos_inmutables)}

    def validar(self, recurso):
        """Rechazo temprano. Por defecto acepta."""
        return None

    @abstractmethod
    def crear(self, recurso):
        """Crea el recurso de forma idempotente y devuelve su estado real (dict)."""

    @abstractmethod
    def leer(self, recurso, real):
        """Devuelve el estado real actual, o None si el recurso ya no existe.
        Lanza ErrorTransitorio si no se pudo consultar."""

    def actualizar(self, recurso, real):
        raise RequiereReemplazo(f"{self.tipo} no admite cambios en caliente")

    @abstractmethod
    def destruir(self, recurso, real):
        """Elimina el recurso. Idempotente: si ya no existe, termina sin error."""

    def adoptar(self, marca_propiedad):
        """Busca recursos reales por su marca. Por defecto no hay búsqueda."""
        return []


class FamiliaProveedores(ABC):
    """Fábrica Abstracta: un método de construcción por cada producto de la familia."""
    nombre = None

    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self._cache = {}

    @abstractmethod
    def crear_conmutacion(self): ...
    @abstractmethod
    def crear_red(self): ...
    @abstractmethod
    def crear_volumen(self): ...
    @abstractmethod
    def crear_puerto(self): ...
    @abstractmethod
    def crear_computo(self): ...
    @abstractmethod
    def crear_acceso(self): ...
    @abstractmethod
    def crear_consola(self): ...

    @abstractmethod
    def capacidades(self):
        """Lo que la familia declara poder hacer (imágenes, tipos de recurso, etc.)."""

    def producto(self, tipo):
        """Devuelve (y reutiliza) el producto de la familia para un tipo de recurso."""
        if tipo not in self._cache:
            constructor = getattr(self, f"crear_{tipo}", None)
            if constructor is None:
                raise ErrorPermanente(f"la familia {self.nombre} no ofrece '{tipo}'")
            self._cache[tipo] = constructor()
        return self._cache[tipo]
