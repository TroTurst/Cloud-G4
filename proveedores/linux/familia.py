"""Familia Linux · fábrica concreta de la Fábrica Abstracta (R2)."""
from ..contrato import FamiliaProveedores
from .remoto import Remoto
from .productos import (ConmutacionLinux, RedLinux, VolumenLinux, PuertoLinux,
                        ComputoLinux, AccesoLinux, ConsolaLinux)


class FabricaLinux(FamiliaProveedores):
    nombre = "linux"

    def __init__(self, cfg, log):
        super().__init__(cfg, log)
        self.remoto = Remoto(cfg, log)

    def crear_conmutacion(self):
        return ConmutacionLinux(self.cfg, self.remoto, self.log)

    def crear_red(self):
        return RedLinux(self.cfg, self.remoto, self.log)

    def crear_volumen(self):
        return VolumenLinux(self.cfg, self.remoto, self.log)

    def crear_puerto(self):
        return PuertoLinux(self.cfg, self.remoto, self.log)

    def crear_computo(self):
        return ComputoLinux(self.cfg, self.remoto, self.log)

    def crear_acceso(self):
        return AccesoLinux(self.cfg, self.remoto, self.log)

    def crear_consola(self):
        return ConsolaLinux(self.cfg, self.remoto, self.log)

    def capacidades(self):
        return {
            "imagenes": list(self.cfg["imagenes"]["catalogo"]),
            "tipos": ["conmutacion", "red", "volumen", "puerto", "computo", "acceso", "consola"],
            "aislamiento": "vlan-802.1q",
            "servidores": [s["nombre"] for s in self.cfg["servidores"]],
        }
