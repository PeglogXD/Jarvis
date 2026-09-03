# NVIDIA RAG Blueprint — Skill Reference

## Purpose
Use this skill for NVIDIA RAG Blueprint operations: deployment, configuration,
troubleshooting, shutdown, and feature management across Docker, Helm, and
library deployments.

## Key Concepts

### What is RAG?
Retrieval-Augmented Generation (RAG) enhances LLM responses by retrieving relevant
documents from a vector database before generating answers. This allows the model
to answer questions about specific data it wasn't trained on.

### NVIDIA RAG Blueprint Components
- **RAG Server** — FastAPI server that orchestrates retrieval + generation
- **Ingestor Server** — Processes and indexes documents into vector DB
- **Vector DB** — Milvus, Elasticsearch, or LanceDB for document storage
- **NIM Services** — Self-hosted or NVIDIA-hosted LLM/embedding/ranking models
- **Guardrails** — NeMo Guardrails for safety filtering
- **Observability** — Zipkin, Grafana, Prometheus for monitoring

### Deployment Modes
| Mode | Description |
|------|-------------|
| Docker Compose | Local containers via `docker compose` |
| Kubernetes/Helm | Production-grade K8s deployments |
| Library Mode | Python library, no containers |

### Quick Deploy
```bash
# 1. Clone the repo
git clone https://github.com/NVIDIA-AI-Blueprints/rag.git
cd rag

# 2. Set NGC API key
export NGC_API_KEY="your-ngc-key"

# 3. Deploy with Docker Compose
docker compose -f deploy/compose/docker-compose-rag-server.yaml up -d
docker compose -f deploy/compose/docker-compose-ingestor-server.yaml up -d
docker compose -f deploy/compose/vectordb.yaml up -d

# 4. Check health
curl -s http://localhost:8081/v1/health?check_dependencies=true
```

### Feature Configuration
| Feature | Keywords | Config |
|---------|----------|--------|
| VLM (Vision) | vlm, image captioning | `ENABLE_VLM=true` |
| Guardrails | safety, guardrails | `ENABLE_GUARDRAILS=true` |
| Agentic RAG | agent, planning | `ENABLE_AGENTIC_RAG=true` |
| Query Rewriting | rewrite, decompose | `ENABLE_QUERY_REWRITE=true` |
| Hybrid Search | search, retrieval | `SEARCH_TYPE=hybrid` |
| Reasoning | think, reasoning | `ENABLE_REASONING=true` |
| Summarization | summarize | `ENABLE_SUMMARIZATION=true` |

### Common Troubleshooting
- Services not running → check `docker ps`, ensure Docker is started
- Health check fails → check logs: `docker logs rag-server`
- Port conflicts → check `docs/service-port-gpu-reference.md`
- GPU OOM → reduce model size or use more GPUs

### Hardware Requirements
- Minimum: 1 GPU with 24GB+ VRAM (e.g., RTX 3090/4090)
- Recommended: 2+ GPUs (LLM + embedding/ranking)
- B200: No VLM, No Guardrails restrictions
- CPU-only: Library mode only, limited performance

### Python Library Usage
```python
from nvidia_rag import RAGPipeline

rag = RAGPipeline(
    llm_model="nvidia/llama-3.1-nemotron-70b-instruct",
    embedding_model="nvidia/nv-embedqa-e5-v5",
    vector_db="milvus"
)

# Ingest documents
rag.ingest(documents=["doc1.pdf", "doc2.txt"])

# Query
response = rag.query("What is RAG?")
print(response.answer)
```

### API Endpoints
- `POST /v1/query` — Query the RAG pipeline
- `POST /v1/ingest` — Ingest documents
- `GET /v1/health` — Health check
- `GET /v1/collections` — List document collections
- `DELETE /v1/collections/{name}` — Delete a collection
