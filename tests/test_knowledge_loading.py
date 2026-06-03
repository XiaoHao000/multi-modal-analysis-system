from rag.knowledge_base import load_business_knowledge, BUSINESS_KNOWLEDGE


def test_business_knowledge_is_non_empty():
    items = BUSINESS_KNOWLEDGE
    assert isinstance(items, list)
    assert len(items) > 0
    assert all(isinstance(item, str) for item in items)


def test_fallback_is_the_default_list():
    import rag.knowledge_base as kb
    assert len(kb._DEFAULT_KNOWLEDGE) >= 8


def test_load_knowledge_returns_list():
    items = load_business_knowledge()
    assert isinstance(items, list)
    assert len(items) > 0
