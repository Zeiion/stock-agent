from app.sentiment import score_text, score_headlines


def test_bullish():
    r = score_text("Stock surges to record high, analysts upgrade to buy")
    assert r["label"] == "bullish"
    assert r["score"] > 0
    assert r["bull"] >= 2


def test_bearish_chinese():
    r = score_text("公司业绩大跌，遭机构减持，盘中暴跌")
    assert r["label"] == "bearish"
    assert r["score"] < 0
    assert r["bear"] >= 2


def test_neutral_and_bounds():
    r = score_text("The company held its annual meeting today")
    assert r["label"] == "neutral"
    assert -1.0 <= r["score"] <= 1.0


def test_robust_to_bad_input():
    assert score_text("")["label"] == "neutral"
    assert score_text(None)["label"] == "neutral"  # type: ignore[arg-type]


def test_score_headlines_aggregate():
    items = [
        {"title": "Stock soars, beats estimates", "summary": ""},
        {"title": "Shares plunge on lawsuit and downgrade", "summary": ""},
        {"title": "Company announces routine update", "summary": ""},
    ]
    out = score_headlines(items)
    assert len(out["items"]) == 3
    assert all("sentiment" in it for it in out["items"])
    agg = out["aggregate"]
    assert agg["n"] == 3
    assert -1.0 <= agg["score"] <= 1.0
    assert agg["label"] in ("bullish", "bearish", "neutral")


def test_score_headlines_empty():
    out = score_headlines([])
    assert out["aggregate"]["n"] == 0
    assert out["items"] == []
