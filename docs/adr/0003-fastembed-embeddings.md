# ADR 0003 — fastembed instead of sentence-transformers for local embeddings

Status: Accepted (2026-10-18, delegated decision)

Context: CLAUDE.md names `sentence-transformers`, which pulls in PyTorch (large, CUDA wheels, slow Windows installs). Spike F goal was a free, local, light embedding provider.

Decision: use `fastembed` (ONNX runtime) with `BAAI/bge-small-en-v1.5` (384 dimensions), behind the unchanged `EmbeddingProvider` port. Verified on this machine: model downloads once from the public Hugging Face hub (no client data sent), embeds in-process; sample cosine 0.91 for a paraphrase pair vs 0.61 for an unrelated pair. English only (matches decision of 2026-10-18).

Consequences: much smaller install; swapping to sentence-transformers or an API later only needs a new adapter. Similarity thresholds are model-specific and must be re-measured on the eval set when the model changes. Vector column is `vector(384)`; changing models requires a migration plus re-embedding.
