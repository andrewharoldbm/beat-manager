"""Artist names that are also everyday words / genre words. Searching '<word> type beat' returns
generic beats, so their numbers are meaningless - such artists are hidden from research and titles."""
from app.common.genres import CANON

WORDS = set("""
vibes vibe love life money night dark sad chill mood moods dream dreams lost alone happy angel angels heaven
hell fire ice cold rain summer winter spring fall drip wave waves gold diamond diamonds
king queen boss god ghost ghosts demon demons star stars moon sun sky blue red black white
pain soul spirit hope faith peace war city street streets home free young old new real
trap drill rage plugg cloud jerk lofi lo-fi boom bap rnb soul jazz pop rock emo punk sample
piano guitar bass melody melodies beat beats instrumental instrumentals music song songs
intro outro freestyle remix hard slow fast crazy wild lit fire hype energy energetic
aggressive melodic emotional dreamy ambient underground experimental uptempo bounce bouncy
nostalgia nostalgic lonely heartbreak broken toxic party club late nights weekend
""".split())


def is_generic(name: str) -> bool:
    n = (name or "").strip().lower()
    return n in WORDS or n in CANON or n.rstrip("s") in WORDS

STOP = set("""you me i we us they he she it the a an and or of for to in on at by with my your our
x vs feat ft official music video audio type beat beats instrumental profit free rap hiphop
""".split())
GENRE_WORDS = set("""hip hop hiphop boom bap boombap old school oldschool rnb r&b soul jazz jazzy lofi lo-fi indie alt alternative
pop rock edm house techno metal country gospel funk funky disco trap drill rage plugg cloud jerk drill
underground experimental chill smooth fast slow flow hard dark sad emotional emotions melodic vibes vibe
guitar piano sample sampled 90s 80s 70s 2000s classic west east coast south dirty uk ny us
""".split())
STRONG = ("hip hop", "boom bap", "boombap", "old school", "oldschool", "r&b", "type beat", "for profit",
          "instrumental", "lofi", "lo-fi", "indie")


def is_junk(name: str) -> bool:
    """Tag-like or word-like 'artists' from Last.fm/YouTube (e.g. '90s Old School Boom Bap', 'you', '** Smooth')."""
    import re
    raw = (name or "").strip()
    n = raw.lower()
    if not raw or not raw[0].isalnum():
        return True
    if is_generic(raw):
        return True
    if any(p in n for p in STRONG):
        return True
    toks = [t for t in re.split(r"[\s/,&+]+", n) if t]
    return all(t in STOP or t in GENRE_WORDS or t in WORDS or re.fullmatch(r"\d+s?", t) for t in toks)
