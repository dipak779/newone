#!/usr/bin/env python3
"""Rebuild app/words.txt: real, common English words only.

Rules
  - 3-6 letters: any real word that people actually use (wordfreq zipf >= MIN_ZIPF_SHORT)
  - 7-10 letters: only well-known words (zipf >= MIN_ZIPF_LONG), e.g. craziest, upcoming, embassy
  - must be a dictionary word or a normal form of one (guys, dreamed, craziest, trolling)
  - names/places are dropped (first-name + country/city lists) unless very common words (hope, grace), as is profanity
Order: SEEDS first, then 5-6 letters, 7-10 letters, 3-4 letters (Telegram needs 5+, so those are Instagram-only).

    pip install wordfreq english-words && python tools/build_words.py
"""
import random
import re
from pathlib import Path

import os

import geonamescache
import names as names_pkg
from english_words import get_english_words_set
from wordfreq import top_n_list, zipf_frequency

MIN_ZIPF_SHORT = 2.9
MIN_ZIPF_LONG = 3.1
SEEDS = """guys deed ominous half heck dreamed urgent craziest upcoming trolling embassy emotion wept
showoff manner therapy money cash save pleased karma""".split()
BLOCK = set("""fuck fucks fucked fucker fucking shit shits shitty bitch bitches cunt cunts dick dicks cock cocks
pussy porn porno sex sexy nude nudes rape raped rapist slut sluts whore whores nigga nigger fag fags faggot
retard retards tits boobs anal penis vagina dildo horny cum jizz wank twat bastard nazi nazis hitler isis
vader tilly sanders mon tue tues wed thu thur thurs fri sat jan feb mar apr jun jul aug sep sept oct nov dec etc""".split())

# real words that are also big places / countries: keep them
PLACE_WORDS = set("""turkey guinea phoenix jersey chile china nice reading bath mobile surprise
victoria jordan georgia ivory jade orange essen""".split())

OUT = Path(__file__).resolve().parent.parent / "app" / "words.txt"


def base_forms(w: str):
    """Possible dictionary forms of an inflected word."""
    yield w
    for suf, rep in (("ies", "y"), ("es", ""), ("s", ""), ("ied", "y"), ("ed", ""), ("ed", "e"), ("d", ""),
                     ("ing", ""), ("ing", "e"), ("iest", "y"), ("est", ""), ("est", "e"), ("ier", "y"),
                     ("er", ""), ("er", "e"), ("ly", ""), ("ily", "y")):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            stem = w[: -len(suf)] + rep
            yield stem
            if len(stem) >= 4 and stem[-1] == stem[-2] and stem[-1] not in "aeiouy":  # trolling->troll(l)
                yield stem[:-1]


def main():
    lower = get_english_words_set(["gcide"], lower=True, alpha=True)
    web2 = get_english_words_set(["web2"], lower=False, alpha=True)
    web2_lower = {w for w in web2 if w.islower()}
    proper = set()
    d = os.path.dirname(names_pkg.__file__)
    for f in ("dist.male.first", "dist.female.first"):
        proper |= {line.split()[0].lower() for line in open(os.path.join(d, f)) if line.strip()}
    g = geonamescache.GeonamesCache()
    big_places = {c["name"].lower() for c in g.get_countries().values()}
    big_places |= {c["name"].lower() for c in g.get_cities().values() if c.get("population", 0) >= 500_000}
    big_places -= PLACE_WORDS
    proper |= {c["name"].lower() for c in g.get_cities().values()}
    words = []
    for w in top_n_list("en", 100000):
        if not re.fullmatch(r"[a-z]{3,10}", w) or w in BLOCK:
            continue
        z = zipf_frequency(w, "en")
        if z < (MIN_ZIPF_SHORT if len(w) <= 6 else MIN_ZIPF_LONG):
            continue
        if w not in SEEDS:
            if w not in web2_lower and w.capitalize() in web2:  # only ever capitalised: john, london, paris
                continue
            if w in big_places:  # london, paris, japan
                continue
            if w in proper and z < 3.5:  # rarer first names / cities: collin, tilly, croydon
                continue
        if any(b in lower for b in base_forms(w)):
            words.append(w)
    words = sorted(set(words))
    rng = random.Random(42)
    tiers = [[w for w in words if 5 <= len(w) <= 6], [w for w in words if len(w) >= 7],
             [w for w in words if len(w) <= 4]]
    for t in tiers:
        rng.shuffle(t)
    ordered = list(dict.fromkeys(SEEDS + [w for t in tiers for w in t]))
    header = ("# Real, common English words (built by tools/build_words.py). One per line, # = comment.\n"
              "# Order matters: the hunter checks from the top. Add your own words near the top.\n")
    OUT.write_text(header + "\n".join(ordered) + "\n", encoding="utf-8")
    print(f"{len(ordered)} words -> {OUT}  (5-6: {len(tiers[0])}, 7-10: {len(tiers[1])}, 3-4: {len(tiers[2])})")


if __name__ == "__main__":
    main()
