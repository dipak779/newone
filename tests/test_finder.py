"""Offline tests: HTTP is replaced by canned responses."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import username_finder as uf  # noqa: E402


def fake(routes):
    def fetch(url, headers):
        for prefix, resp in routes.items():
            if url.startswith(prefix):
                return resp
        raise AssertionError(f"unexpected URL {url}")
    return fetch


R = uf.Response


def test_telegram_rules():
    assert uf.check_telegram("cash").status == uf.INVALID          # too short
    assert uf.check_telegram("1money").status == uf.INVALID        # starts with digit
    assert uf.check_telegram("mon__ey").status == uf.INVALID       # double underscore
    assert uf.check_telegram("money_").status == uf.INVALID        # trailing underscore


def test_telegram_taken():
    f = fake({"https://t.me/": R(200, '<div class="tgme_page_title">Money</div>', "https://t.me/money")})
    assert uf.check_telegram("money", f).status == uf.TAKEN


def test_telegram_available_not_on_fragment():
    f = fake({
        "https://t.me/": R(200, "<html>If you have Telegram, you can contact</html>", "https://t.me/zqxkv"),
        "https://fragment.com/": R(200, "", "https://fragment.com/?query=zqxkv"),
    })
    assert uf.check_telegram("zqxkv", f).status == uf.AVAILABLE


def test_telegram_on_fragment():
    page = '<span class="tm-section-header-status tm-status-unavail">Sold</span>'
    f = fake({
        "https://t.me/": R(200, "<html></html>", "https://t.me/karma"),
        "https://fragment.com/": R(200, page, "https://fragment.com/username/karma"),
    })
    res = uf.check_telegram("karma", f)
    assert (res.status, res.detail) == (uf.FRAGMENT, "Sold")


def test_instagram():
    url = "https://www.instagram.com/"
    taken = json.dumps({"data": {"user": {"edge_followed_by": {"count": 42}}}})
    assert uf.check_instagram("money", fake({url: R(200, taken, url)})).status == uf.TAKEN
    assert uf.check_instagram("zqxkv", fake({url: R(404, "", url)})).status == uf.AVAILABLE
    assert uf.check_instagram("zqxkv", fake({url: R(429, "", url)})).status == uf.UNKNOWN
    assert uf.check_instagram("bad..name").status == uf.INVALID


def test_candidates_and_wordlist():
    words = uf.load_words(uf.DEFAULT_WORDS)
    assert "karma" in words and len(words) == len(set(words))
    picks = uf.pick_candidates(words, 10, 5, 7, seed=1)
    assert len(picks) == 10 and all(5 <= len(w) <= 7 for w in picks)
    assert picks == uf.pick_candidates(words, 10, 5, 7, seed=1)


def test_run_and_save(tmp_path):
    url = "https://www.instagram.com/"
    out = uf.run(["money", "cash"], ["instagram"], 0, fetch=fake({url: R(404, "", url)}))
    assert [r.status for r in out] == [uf.AVAILABLE, uf.AVAILABLE]
    uf.save(out, tmp_path / "r.csv")
    assert "money,instagram,AVAILABLE" in (tmp_path / "r.csv").read_text()
