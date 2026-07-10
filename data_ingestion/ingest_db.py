import chromadb
from chromadb.config import Settings
from pathlib import Path

# Anchor the vector store to Tarrifpilot/Database/chroma_db regardless of CWD.
_ROOT = Path(__file__).resolve().parents[1]          # Tarrifpilot/
_CHROMA_PATH = str(_ROOT / "Database" / "chroma_db")


def get_chroma_collection():
    client = chromadb.PersistentClient(path=_CHROMA_PATH)

    collection = client.get_or_create_collection(
        name="hs_taxonomy",        metadata={"hnsw:space": "cosine"}
    )
    return collection


if __name__ == "__main__":
    collection = get_chroma_collection()
    print(
        f"✓ ChromaDB initialized. Collection 'hs_taxonomy' count: {collection.count()}")
