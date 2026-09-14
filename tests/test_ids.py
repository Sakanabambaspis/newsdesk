from newsdesk.core.ids import (canonical_url, content_hash, hamming_distance,
                               item_id_for, simhash64)


def test_canonical_url_strips_tracking_and_normalizes():
    url = "https://Example.com/story/?utm_source=rss&fbclid=x&b=2&a=1#top"
    assert canonical_url(url) == "https://example.com/story?a=1&b=2"


def test_canonical_url_strips_default_port_and_trailing_slash():
    assert canonical_url("https://example.com:443/story/") == "https://example.com/story"
    assert canonical_url("http://example.com:80/") == "http://example.com/"


def test_canonical_url_keeps_meaningful_port():
    assert canonical_url("http://localhost:8080/feed") == "http://localhost:8080/feed"


def test_content_hash_ignores_whitespace_differences():
    a = content_hash(title="T", text="alpha beta\n\ngamma")
    b = content_hash(title=" T ", text="alpha   beta gamma")
    assert a == b


def test_content_hash_changes_with_wording():
    a = content_hash(title="T", text="alpha beta")
    b = content_hash(title="T", text="alpha beta changed")
    assert a != b


def test_item_id_is_deterministic_and_url_stable():
    one = item_id_for("https://example.com/a?utm_source=x")
    two = item_id_for("https://example.com/a")
    assert one == two
    assert one.startswith("item_")


def test_simhash_near_duplicates_come_close():
    a = simhash64("grid operator declares emergency as heat wave strains network")
    b = simhash64("grid operator declares emergency as heat wave strains the network")
    far = simhash64("chip export rules tighten ahead of trade talks next quarter")
    assert hamming_distance(a, b) <= 4
    assert hamming_distance(a, far) > 4
