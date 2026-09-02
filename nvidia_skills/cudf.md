# cuDF & dask-cuDF Implementer's Guide (NVIDIA Skill)

## Compatibility
- Requires NVIDIA Volta or newer on CUDA 12, or Turing or newer on CUDA 13.
- Release 26.04 supports CUDA 12.2-12.9 with driver 535+ or CUDA 13.0-13.1 with driver 580+, and Python 3.11-3.14.
- cuDF sweet spot: >100K rows.

## Role
Eres un experto en cuDF ayudando a trabajar con GPU DataFrames. El usuario entiende pandas y sus datos — tu trabajo es guiarlos a código GPU correcto y rápido con mínimo fricción.

## Critical Rules
1. **Elegir el camino correcto:** `cudf.pandas` para compatibilidad amplia, cuDF explícito para migraciones y optimización de hot paths.
2. **Tamaño mínimo: 100K filas.** Debajo, la sobrecarga de transferencia GPU supera la aceleración.
3. **Conversiones en fronteras:** Usar `.to_pandas()` solo para display, plotting, o salida final.
4. **Float32 es tu amigo.** cuDF en float64 es más lento; castear temprano cuando la precisión lo permita.
5. **Validar semánticas** en slices representativos antes de afirmar paridad con pandas.

## Three Paths to GPU DataFrames

### Path 1: cudf.pandas (Compatibilidad / Mínimo Cambio)
```python
# Jupyter
%load_ext cudf.pandas
import pandas as pd  # ahora GPU-backed

# Script
# python -m cudf.pandas my_script.py
```

### Path 2: cuDF API Explícito
```python
import cudf
df = cudf.read_parquet("data.parquet")
result = df.groupby("key")["value"].sum()
merged = df.merge(lookup, on="id", how="left")
filtered = df[df["amount"] > 1000]
df["clean"] = df["name"].str.strip().str.lower()
```

### Path 3: dask-cuDF (Multi-GPU / Datos Grandes)
```python
from dask_cuda import LocalCUDACluster
from dask.distributed import Client
import dask_cudf

cluster = LocalCUDACluster(enable_cudf_spill=True)
client = Client(cluster)
ddf = dask_cudf.read_parquet("s3://bucket/data/*.parquet")
result = ddf.groupby("key").agg({"value": "sum"}).compute()
```

## Memory Management
```python
import cudf
cudf.set_option("spill", True)  # spill to host RAM when GPU is full

# RMM pool allocator
import rmm
rmm.set_current_device_resource(rmm.mr.CudaAsyncMemoryResource())
```

| GPU Free vs Dataset | Strategy |
|---|---|
| Free > 2× dataset | Single GPU cuDF |
| Free 1–2× dataset | cuDF + spill |
| Dataset > GPU mem | dask-cuDF |
| Dataset > node mem | dask-cuDF multi-node |

## Troubleshooting
- **Sin speedup:** Datos < 100K filas? GPU overhead domina. Medir con working set más grande.
- **OOM:** 1) `cudf.set_option("spill", True)` 2) RMM allocator 3) Mover a dask-cuDF
- **AttributeError:** Verificar API gaps, mantener esa operación en CPU
- **Resultados diferentes vs pandas:** Null/NaN handling difiere (cuDF usa `<NA>`, pandas usa `NaN`)

## Reference
- **cuDF Docs:** https://docs.rapids.ai/api/cudf/stable/
- **GitHub:** https://github.com/rapidsai/cudf
