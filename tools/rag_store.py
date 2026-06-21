"""RAG knowledge store (Chroma + free local embeddings).

Grounds the Researcher in a curated reference corpus so the Fact-Checker has
something concrete to verify claims against. Member 1 (Vinayak Paka).

The sample corpus below is a placeholder — replace ``SAMPLE_CORPUS`` with your
project's real reference documents for the demo.
"""
from __future__ import annotations

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
PERSIST_DIR = ".chroma"
COLLECTION = "citewise"

# (text, source_url) reference snippets used to seed the vector store.
SAMPLE_CORPUS: list[tuple[str, str]] = [
    (
        "Solar photovoltaic module costs fell by roughly 90% between 2010 and "
        "2020, making solar one of the cheapest sources of new electricity "
        "generation in many parts of the world.",
        "https://en.wikipedia.org/wiki/Growth_of_photovoltaics",
    ),
    (
        "Photosynthesis is the process by which green plants, algae and some "
        "bacteria convert light energy, water and carbon dioxide into glucose "
        "and oxygen. It is the primary source of oxygen in Earth's atmosphere.",
        "https://en.wikipedia.org/wiki/Photosynthesis",
    ),
    (
        "Large language models are trained on large text corpora and can "
        "hallucinate — producing fluent but factually incorrect statements — "
        "which is why grounding outputs in retrievable sources matters.",
        "https://en.wikipedia.org/wiki/Hallucination_(artificial_intelligence)",
    ),
    (
        "The Great Wall of China is a series of fortifications built over many "
        "centuries, not a single continuous wall; contrary to a popular myth it "
        "is not visible to the naked eye from space.",
        "https://en.wikipedia.org/wiki/Great_Wall_of_China",
    ),
    (
        "Wind and solar are variable renewable sources, so grid operators pair "
        "them with storage and flexible generation to balance supply and demand "
        "as their share of electricity grows.",
        "https://en.wikipedia.org/wiki/Variable_renewable_energy",
    ),
]

_embeddings = None
_store = None


def _get_embeddings():
    global _embeddings
    if _embeddings is None:
        from langchain_huggingface import HuggingFaceEmbeddings

        _embeddings = HuggingFaceEmbeddings(model_name=EMBED_MODEL)
    return _embeddings


def get_store():
    """Return the (lazily created, persisted) Chroma store."""
    global _store
    if _store is None:
        from langchain_chroma import Chroma

        _store = Chroma(
            collection_name=COLLECTION,
            embedding_function=_get_embeddings(),
            persist_directory=PERSIST_DIR,
        )
    return _store


def seed_sample_corpus() -> int:
    """Seed the store with ``SAMPLE_CORPUS`` if empty. Returns docs added."""
    from langchain_core.documents import Document

    store = get_store()
    try:
        existing = len(store.get().get("ids", []))
    except Exception:
        existing = 0
    if existing:
        return 0

    docs = [
        Document(page_content=text, metadata={"source": url})
        for text, url in SAMPLE_CORPUS
    ]
    store.add_documents(docs)
    return len(docs)


def retrieve(query: str, k: int = 3):
    """Return the top-k most relevant reference documents for ``query``."""
    return get_store().similarity_search(query, k=k)
