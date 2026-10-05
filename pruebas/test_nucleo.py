"""Pruebas del núcleo con la familia Simulada (no tocan servidores).
Ejecutar desde la raíz:  python3 -m unittest discover -s pruebas -v
"""
import json
import logging
import tempfile
import unittest

from nucleo import conductor
from nucleo.modelo import ErrorValidacion, validar
from nucleo.plantillas import plantilla, ex1
from proveedores.simulada import recursos_simulados


class PruebasNucleo(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="orq-prueba-")
        conductor.iniciar(directorio_estado=self.dir)
        for h in logging.getLogger("orq").handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                h.setLevel(logging.ERROR)
        conductor._c().cfg["simulada"]["fallar_en"] = []

    def pools(self):
        return json.load(open(f"{self.dir}/pools.json"))

    def test_plantillas(self):
        lin = plantilla("lineal").generar("l", 4)
        ani = plantilla("anillo").generar("a", 4)
        self.assertEqual(len(lin.enlaces), 3)
        self.assertEqual(len(ani.enlaces), 4)
        e = ex1()
        self.assertEqual(len(e.nodos), 6)
        self.assertEqual(len(e.enlaces), 6)
        self.assertEqual(sum(n.acceso for n in e.nodos), 2)

    def test_validacion(self):
        spec = plantilla("lineal").generar("l", 2)
        spec.enlaces[0].b = "noexiste"
        with self.assertRaises(ErrorValidacion):
            validar(spec, conductor._c().cfg)

    def test_desplegar_y_eliminar(self):
        r = conductor.desplegar(ex1(), zona="simulada")
        self.assertEqual(r["estado"], "Activo")
        self.assertEqual(len(set(r["salidas"][n]["servidor"] for n in r["salidas"])), 4)
        r2 = conductor.eliminar(r["id"])
        self.assertEqual(r2["estado"], "Eliminado")
        self.assertTrue(all(len(v) == 0 for v in self.pools().values()))

    def test_compensacion(self):
        conductor._c().cfg["simulada"]["fallar_en"] = ["cmp:VM5"]
        r = conductor.desplegar(ex1(), zona="simulada")
        self.assertEqual(r["estado"], "Fallido")
        self.assertFalse([k for k in recursos_simulados() if k.startswith(r["id"])])
        self.assertTrue(all(len(v) == 0 for v in self.pools().values()))

    def test_sin_colisiones(self):
        a = conductor.desplegar(plantilla("anillo").generar("a", 4), zona="simulada")
        b = conductor.desplegar(plantilla("lineal").generar("l", 3), zona="simulada")
        vlans = self.pools()["vlan"]
        self.assertEqual(sorted(set(vlans.values())), sorted({a["id"], b["id"]}))
        self.assertEqual(len(vlans), 4 + 2)

    def test_idempotencia(self):
        r = conductor.desplegar(plantilla("lineal").generar("l", 3), zona="simulada")
        r2 = conductor.desplegar(plantilla("lineal").generar("l", 3), zona="simulada", slice_id=r["id"])
        self.assertEqual(r2["acciones"], 0)


if __name__ == "__main__":
    unittest.main()
