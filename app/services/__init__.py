# app/services/__init__.py
# Re-export build_retriever_bundle from the original services module
# This preserves backward compatibility with app.state importing from app.services

from typing import Dict, Any
import os
from llama_index.core import StorageContext, VectorStoreIndex, Settings
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore
import chromadb


def build_retriever_bundle(paths: Dict[str, str], tenant: str, index: str) -> dict:
    """
    Minimal vector-only retriever over Chroma for the specified tenant/index.
    Expects embeddings already upserted by the indexer.
    """
    # Vector store
    chroma_client = chromadb.PersistentClient(path=paths["chroma_path"])
    collection = chroma_client.get_or_create_collection("docs", metadata={"tenant": tenant})
    vector_store = ChromaVectorStore(chroma_collection=collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    # Embeddings (for query side similarity)
    Settings.embed_model = OpenAIEmbedding(
        model=os.getenv("EMBED_MODEL", "text-embedding-3-large")
    )

    # Create a VectorStoreIndex bound to this store
    vindex = VectorStoreIndex.from_vector_store(vector_store=vector_store, storage_context=storage_context)

    retriever = vindex.as_retriever(similarity_top_k=6)

    return {
        "retriever": retriever,
        "settings": {
            "tenant": tenant,
            "index": index,
            "chroma_path": paths["chroma_path"],
            "chunks_path": paths["chunks_path"],
        },
    }
