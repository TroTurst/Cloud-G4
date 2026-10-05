"""Funciones de demostración de la primera entrega (sin APIs, solo funciones).

Uso desde la raíz del repositorio:
    python3 -m ejemplos.demo lineal 3                 # despliega una lineal de 3 VMs en el cluster
    python3 -m ejemplos.demo anillo 4                 # despliega un anillo de 4 VMs
    python3 -m ejemplos.demo ex1                      # despliega EX1-Topology
    python3 -m ejemplos.demo listar
    python3 -m ejemplos.demo estado <id>
    python3 -m ejemplos.demo eliminar <id>
    python3 -m ejemplos.demo plan ex1                 # muestra el plan sin ejecutar nada
    python3 -m ejemplos.demo concurrente              # dos slices a la vez

Opciones: --zona simulada (no toca servidores) · --seco (registra los comandos sin ejecutarlos)
          --flavor cirros-small|ubuntu-small · --acceso vm1,vm3
"""
import argparse
import json
import threading

from nucleo import conductor
from nucleo.plantillas import plantilla, ex1


def desplegar_lineal(n, flavor="cirros-small", acceso=(), zona="linux"):
    spec = plantilla("lineal").generar(f"lineal-{n}", n, flavor=flavor, acceso=acceso)
    return conductor.desplegar(spec, zona=zona)


def desplegar_anillo(n, flavor="cirros-small", acceso=(), zona="linux"):
    spec = plantilla("anillo").generar(f"anillo-{n}", n, flavor=flavor, acceso=acceso)
    return conductor.desplegar(spec, zona=zona)


def desplegar_ex1(zona="linux"):
    return conductor.desplegar(ex1(), zona=zona)


def desplegar_concurrente(zona="linux"):
    """Despliega una lineal de 3 y un anillo de 3 al mismo tiempo (prueba de pedidos concurrentes)."""
    resultados = {}

    def tarea(clave, funcion, *args):
        resultados[clave] = funcion(*args)

    hilos = [threading.Thread(target=tarea, args=("lineal", desplegar_lineal, 3, "cirros-small", (), zona)),
             threading.Thread(target=tarea, args=("anillo", desplegar_anillo, 3, "cirros-small", (), zona))]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    return resultados


def _imprimir(x):
    print(json.dumps(x, indent=2, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description="Demo R2: despliegue de slices en el cluster Linux")
    ap.add_argument("accion", choices=["lineal", "anillo", "ex1", "listar", "estado", "eliminar",
                                        "plan", "concurrente"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--zona", default="linux")
    ap.add_argument("--seco", action="store_true", help="no ejecuta comandos, solo los registra")
    ap.add_argument("--flavor", default="cirros-small")
    ap.add_argument("--acceso", default="", help="nodos con red de acceso, separados por coma")
    a = ap.parse_args()
    conductor.iniciar(**({"modo_seco": True} if a.seco else {}))
    acceso = [x for x in a.acceso.split(",") if x]

    if a.accion == "lineal":
        _imprimir(desplegar_lineal(int(a.arg or 3), a.flavor, acceso, a.zona))
    elif a.accion == "anillo":
        _imprimir(desplegar_anillo(int(a.arg or 4), a.flavor, acceso, a.zona))
    elif a.accion == "ex1":
        _imprimir(desplegar_ex1(a.zona))
    elif a.accion == "listar":
        _imprimir(conductor.listar())
    elif a.accion == "estado":
        _imprimir(conductor.estado_slice(a.arg))
    elif a.accion == "eliminar":
        _imprimir(conductor.eliminar(a.arg))
    elif a.accion == "concurrente":
        _imprimir(desplegar_concurrente(a.zona))
    elif a.accion == "plan":
        tipo = a.arg or "ex1"
        spec = ex1() if tipo == "ex1" else plantilla(tipo).generar(f"{tipo}", 4, flavor=a.flavor, acceso=acceso)
        print("\n".join(conductor.plan_en_seco(spec, a.zona)))


if __name__ == "__main__":
    main()
