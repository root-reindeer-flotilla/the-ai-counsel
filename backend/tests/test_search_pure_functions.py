from backend import search


def test_detect_query_intent_comparison_precedence():
    query = "Compare GPT-4 vs Claude for coding tasks"
    assert search.detect_query_intent(query) == "comparison"


def test_detect_query_intent_research():
    query = "History of cryptography and its evolution"
    assert search.detect_query_intent(query) == "research"


def test_detect_query_intent_current_event():
    query = "Latest Nvidia stock price today"
    assert search.detect_query_intent(query) == "current_event"


def test_detect_query_intent_factual_fallback():
    query = "What is recursion in computer science?"
    assert search.detect_query_intent(query) == "factual"


def test_optimize_search_query_removes_fluff_and_adds_year_for_current_event():
    q = "Can you please tell me about latest OpenAI news"
    out = search.optimize_search_query(q)
    assert out["intent"] == "current_event"
    assert "latest openai news" in out["web_query"].lower()
    assert str(search.CURRENT_YEAR) in out["news_query"]


def test_optimize_search_query_fallback_when_cleaned_too_short():
    q = "Please?"
    out = search.optimize_search_query(q)
    assert out["original_query"] == q
    assert out["web_query"].lower().startswith("please")


def test_optimize_search_query_truncates_to_150_chars():
    q = "x" * 300
    out = search.optimize_search_query(q)
    assert len(out["web_query"]) == 150
    assert len(out["news_query"]) == 150


def test_tokenize_removes_stopwords_and_short_terms():
    tokens = search._tokenize("The AI is in a big box with data science")
    assert "the" not in tokens
    assert "in" not in tokens
    assert "ai" not in tokens  # short token removed
    assert "data" in tokens
    assert "science" in tokens


def test_score_result_relevance_returns_neutral_when_no_query_terms():
    score = search.score_result_relevance(
        {"title": "Anything", "summary": "Anything", "url": "https://example.com"},
        set(),
    )
    assert score == 0.5


def test_score_result_relevance_authority_beats_low_quality():
    terms = search._tokenize("nvidia stock earnings")
    authority_score = search.score_result_relevance(
        {
            "title": "Nvidia earnings update",
            "summary": "Nvidia stock and earnings details",
            "url": "https://reuters.com/markets/nvidia",
        },
        terms,
        intent="current_event",
    )
    low_quality_score = search.score_result_relevance(
        {
            "title": "Nvidia earnings update",
            "summary": "Nvidia stock and earnings details",
            "url": "https://pinterest.com/foo",
        },
        terms,
        intent="current_event",
    )
    assert authority_score > low_quality_score
    assert 0.0 <= authority_score <= 1.0


def test_score_result_relevance_has_current_event_freshness_bonus():
    terms = search._tokenize("openai release")
    fresh = search.score_result_relevance(
        {
            "title": f"OpenAI release {search.CURRENT_YEAR}",
            "summary": "announced today",
            "url": "https://example.com/article",
        },
        terms,
        intent="current_event",
    )
    stale = search.score_result_relevance(
        {
            "title": "OpenAI release",
            "summary": "historic overview",
            "url": "https://example.com/article",
        },
        terms,
        intent="current_event",
    )
    assert fresh > stale


def test_rerank_results_orders_by_relevance_score_descending():
    results = [
        {"title": "Unrelated topic", "summary": "nothing here", "url": "https://example.com/a"},
        {"title": "Python testing with pytest", "summary": "pytest tutorial and testing guide", "url": "https://docs.python.org"},
    ]
    reranked = search.rerank_results(results, "pytest testing python")
    assert reranked[0]["title"] == "Python testing with pytest"
    assert reranked[0]["relevance_score"] >= reranked[1]["relevance_score"]


def test_preprocess_query_removes_role_play_and_noise_phrase():
    q = "Act as a financial analyst and evaluate the theory of Tesla growth"
    cleaned = search._preprocess_query(q)
    assert "financial analyst" not in cleaned.lower()
    assert "evaluate the theory" not in cleaned.lower()


def test_extract_search_keywords_with_stubbed_extractor(monkeypatch):
    class DummyExtractor:
        def extract_keywords(self, _):
            return [
                ("financial analyst", 0.01),
                ("OpenAI roadmap", 0.02),
                ("the", 0.03),
                ("GPT-5 timeline", 0.04),
            ]

    monkeypatch.setattr(search, "get_keyword_extractor", lambda: DummyExtractor())
    result = search.extract_search_keywords("Please act as a financial analyst and explain OpenAI roadmap")
    assert "financial analyst" not in result.lower()
    assert "openai roadmap" in result.lower()
