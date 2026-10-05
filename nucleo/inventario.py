"""K6 · Inventario: servidores del cluster y su uso actual (a partir de los slices registrados)."""
ESTADOS_QUE_OCUPAN = {"Desplegando", "Activo", "Error", "Eliminando"}


def servidores(cfg):
    return [dict(s) for s in cfg["servidores"]]


def uso_por_servidor(cfg, estado, excluir_slice=None):
    """vCPU y RAM ocupadas en cada servidor por los slices que tienen recursos."""
    uso = {s["nombre"]: {"vcpu": 0, "ram_mb": 0} for s in cfg["servidores"]}
    for s in estado.listar_slices():
        if s["id"] == excluir_slice or s.get("estado") not in ESTADOS_QUE_OCUPAN:
            continue
        for nodo, srv in s.get("colocacion", {}).items():
            fl = cfg["flavors"][s["flavores"][nodo]]
            uso[srv]["vcpu"] += fl["vcpu"]
            uso[srv]["ram_mb"] += fl["ram_mb"]
    return uso
