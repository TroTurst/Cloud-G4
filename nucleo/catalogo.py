"""K5 · Catálogo y Fábrica de Plataformas.

Dos piezas distintas en un módulo:
- Registro: qué familias existen, qué zonas atienden y qué capacidades declaran.
- Fábrica: dada una zona, construye la familia completa y coherente de proveedores.
Ningún otro módulo instancia un proveedor.
"""


class Catalogo:
    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self._registro = {}      # zona -> clase de fábrica concreta
        self._instancias = {}

    # -------- Registro
    def registrar(self, zona, clase_fabrica):
        self._registro[zona] = clase_fabrica
        self.log.debug("familia '%s' registrada para la zona '%s'", clase_fabrica.nombre, zona)

    def zonas(self):
        return list(self._registro)

    def capacidades(self, zona):
        return self.familia(zona).capacidades()

    # -------- Fábrica
    def familia(self, zona):
        if zona not in self._registro:
            raise KeyError(f"no hay familia registrada para la zona '{zona}'. Zonas: {self.zonas()}")
        if zona not in self._instancias:
            self._instancias[zona] = self._registro[zona](self.cfg, self.log)
        return self._instancias[zona]


def catalogo_por_defecto(cfg, log):
    """Registro explícito de las familias disponibles en esta entrega.
    Agregar una plataforma = escribir su familia y registrarla aquí; el resto no cambia."""
    from proveedores.linux.familia import FabricaLinux
    from proveedores.simulada import FabricaSimulada
    c = Catalogo(cfg, log)
    c.registrar("linux", FabricaLinux)
    c.registrar("simulada", FabricaSimulada)
    return c
