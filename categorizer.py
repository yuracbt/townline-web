"""Categorization rules ported 1:1 from the TownLine Android app
(Categorizer.java). The feed's own beat wins first, then the feed's URL,
then what the story is actually about; the feed's home town is the fallback.
"""


UKRAINE_SRC = ["ukrain", "pravda", "kyiv"]
UKRAINE_URL = ["pravda", "ukrinform", "kyivindependent", "kyivpost", ".ua/"]
BUSINESS_SRC = ["business", "chamber", "economic", "trade"]
SPORT_SRC = ["sport"]
COMMUNITY_SRC = ["community"]

AB_CITIES = [
    ("calgary", "Calgary"),
    ("edmonton", "Edmonton"),
    ("red deer", "Red Deer"),
    ("lethbridge", "Lethbridge"),
    ("airdrie", "Airdrie"),
    ("grande prairie", "Grande Prairie"),
    ("medicine hat", "Medicine Hat"),
    ("fort mcmurray", "Fort McMurray"),
    ("cochrane", "Cochrane"),
    ("okotoks", "Okotoks"),
    ("spruce grove", "Spruce Grove"),
    ("st. albert", "St. Albert"),
    ("banff", "Banff"),
    ("canmore", "Canmore"),
    ("jasper", "Jasper"),
]

UKRAINE_WORDS = ["ukraine", "ukrainian", "kyiv", "kiev", "zelensky", "zelenskiy"]
EVENT_WORDS = [
    "festival", "concert", "market", "workshop", "fair", "parade",
    "fundraiser", "tournament", "exhibition", "celebration", "ceremony",
    "live music", "trivia", "bingo", "open house", "rodeo", "carnival",
    "powwow", "car show", "art walk",
]
BUSINESS_WORDS = [
    "chamber of commerce", "grand opening", "now open", "now hiring",
    "job fair", "business licence", "business license",
    "business", "economy", "economic", "entrepreneur", "startup",
    "investment", "investor", "stock market", "housing market",
    "job market", "real estate", "inflation", "interest rate", "oil price",
]
SPORT_WORDS = [
    "hockey", "nhl", "flames", "oilers", "stampeders", "elks",
    "soccer", "fifa", "basketball", "raptors", "baseball",
    "blue jays", "tennis", "golf", "pga", "olympics",
    "stanley cup", "grey cup", "world cup", "ufc", "mma",
]
COMMUNITY_WORDS = [
    "community", "neighbourhood", "neighborhood", "volunteer",
    "school board", "city council", "neighbours", "neighbors",
]


def _lower(s):
    return (s or "").lower()


def _any(text, words):
    return any(w in text for w in words)


def _match_city(text):
    for key, label in AB_CITIES:
        if key in text:
            return label
    return None


def categorize(source_name, source_url, title, description):
    """Category for one story. Mirrors Categorizer.categorize exactly."""
    if source_name == "Saved":
        return "Saved"
    s = _lower(source_name)
    u = _lower(source_url)
    t = _lower(title) + " " + _lower(description)

    # 1. the feed's own beat
    if _any(s, UKRAINE_SRC):
        return "Ukraine"
    if _any(s, BUSINESS_SRC):
        return "Business"
    if _any(s, SPORT_SRC):
        return "Sport"
    if _any(s, COMMUNITY_SRC):
        return "Community"

    # 2. the feed's URL speaks when the name doesn't
    if _any(u, UKRAINE_URL):
        return "Ukraine"

    # 3. what the story is about: topics, then places
    if _any(t, UKRAINE_WORDS):
        return "Ukraine"
    if "calgary" in t:
        return "Calgary"
    if "edmonton" in t:
        return "Edmonton"
    if "alberta" in t or _match_city(t) is not None:
        return "Alberta"

    # 4. the feed's home town as fallback (URL first, then name)
    city = _match_city(u) or _match_city(s)
    if city is not None:
        return city

    # 5. general story topics
    if _any(t, EVENT_WORDS):
        return "Events"
    if _any(t, BUSINESS_WORDS):
        return "Business"
    if _any(t, SPORT_WORDS):
        return "Sport"
    if _any(t, COMMUNITY_WORDS):
        return "Community"
    return "News"


def think_source(source_name, source_url):
    """What we think a source is about, from name + URL alone.
    Mirrors Categorizer.thinkSource — shown next to each feed."""
    if source_name == "Saved":
        return "Saved"
    s = _lower(source_name)
    u = _lower(source_url)
    if _any(s, UKRAINE_SRC) or _any(u, UKRAINE_URL):
        return "Ukraine"
    if _any(s, BUSINESS_SRC):
        return "Business"
    if _any(s, SPORT_SRC):
        return "Sport"
    if _any(s, COMMUNITY_SRC):
        return "Community"
    city = _match_city(u) or _match_city(s)
    return city if city is not None else "General"
