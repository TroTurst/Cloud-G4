"""Carga de configuración y registro de eventos (logs) del orquestador."""
import json
import logging
import os

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_POR_DEFECTO = os.path.join(RAIZ, "config", "cluster.json")


def cargar_config(ruta=None, **sobrescribir):
    """Lee la configuración del cluster. `sobrescribir` permite cambiar claves de primer nivel
    (por ejemplo modo_seco=True) sin editar el archivo."""
    ruta = ruta or os.environ.get("ORQ_CONFIG", RUTA_POR_DEFECTO)
    with open(ruta, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.update(sobrescribir)
    if os.environ.get("ORQ_MODO_SECO") == "1":
        cfg["modo_seco"] = True
    if not os.path.isabs(cfg["directorio_estado"]):
        cfg["directorio_estado"] = os.path.join(RAIZ, cfg["directorio_estado"])
    cfg["ssh"]["llave"] = os.path.expanduser(cfg["ssh"]["llave"])
    return cfg


class _FiltroSlice(logging.Filter):
    """Garantiza que todo registro tenga el campo 'slice' (identificador de correlación)."""
    def filter(self, record):
        if not hasattr(record, "slice"):
            record.slice = "-"
        return True


def configurar_logs(cfg):
    """Logs estructurados en consola y en <estado>/logs/orq.log.
    Cada línea lleva el id del slice, que funciona como identificador de correlación."""
    log = logging.getLogger("orq")
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | slice=%(slice)s | %(name)s | %(message)s")
    carpeta = os.path.join(cfg["directorio_estado"], "logs")
    os.makedirs(carpeta, exist_ok=True)
    archivo = logging.FileHandler(os.path.join(carpeta, "orq.log"), encoding="utf-8")
    archivo.setLevel(logging.DEBUG)
    archivo.setFormatter(fmt)
    archivo.addFilter(_FiltroSlice())
    consola = logging.StreamHandler()
    consola.setLevel(logging.INFO)
    consola.setFormatter(fmt)
    consola.addFilter(_FiltroSlice())
    log.addHandler(archivo)
    log.addHandler(consola)
    return log


def log_slice(sid, nombre="orq"):
    """Logger con el id del slice adjunto."""
    return logging.LoggerAdapter(logging.getLogger(nombre), {"slice": sid})
