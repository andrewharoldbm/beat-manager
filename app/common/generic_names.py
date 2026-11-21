"""Artist names that are also everyday words / genre words. Searching '<word> type beat' returns
generic beats, so their numbers are meaningless - such artists are hidden from research and titles."""
from app.common.genres import CANON

WORDS = set("""
vibes vibe love life money night dark sad chill mood moods dream dreams lost alone happy angel angels heaven
hell fire ice cold rain summer winter spring fall smoke drip wave waves gold diamond diamonds
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
