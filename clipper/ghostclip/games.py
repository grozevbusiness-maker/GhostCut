"""Games and the hashtags that help clips of them get found."""
from __future__ import annotations

import re

# key: (name shown in the picker, hashtags, words that identify the game in Twitch's category name)
GAMES: dict[str, tuple[str, list[str], list[str]]] = {
    "fortnite": ("Fortnite", ["#fortnite", "#fortniteclips", "#fortnitememes", "#fortnitebattleroyale", "#epicgames"], ["fortnite"]),
    "valorant": ("Valorant", ["#valorant", "#valorantclips", "#valorantmemes", "#valorantgameplay", "#riotgames"], ["valorant"]),
    # GTA VI first: "Grand Theft Auto VI" must not fall through to the GTA V tags below
    "gta6": ("GTA 6", ["#gta6", "#gtavi", "#gta6gameplay", "#grandtheftauto6", "#gta"],
             ["grand theft auto vi", "grand theft auto 6", "gta vi", "gta 6", "gta6"]),
    "gta": ("GTA V / GTA RP", ["#gta5", "#gtarp", "#gtaonline", "#gta", "#gtavclips"], ["grand theft auto", "gta"]),
    "minecraft": ("Minecraft", ["#minecraft", "#minecraftmemes", "#minecraftclips", "#minecraftbuilds"], ["minecraft"]),
    "lol": ("League of Legends", ["#leagueoflegends", "#lol", "#leagueclips", "#riotgames"], ["league of legends"]),
    "cs2": ("Counter-Strike 2", ["#cs2", "#counterstrike", "#csgo", "#cs2clips"], ["counter-strike", "counter strike"]),
    "cod": ("Call of Duty / Warzone", ["#warzone", "#callofduty", "#cod", "#codclips", "#warzoneclips"], ["call of duty", "warzone"]),
    "apex": ("Apex Legends", ["#apexlegends", "#apex", "#apexclips", "#apexlegendsclips"], ["apex legends"]),
    "rocketleague": ("Rocket League", ["#rocketleague", "#rocketleagueclips", "#rlclips"], ["rocket league"]),
    "overwatch": ("Overwatch 2", ["#overwatch2", "#overwatch", "#overwatchclips"], ["overwatch"]),
    "marvelrivals": ("Marvel Rivals", ["#marvelrivals", "#marvelrivalsclips", "#marvel"], ["marvel rivals"]),
    "dota": ("Dota 2", ["#dota2", "#dota", "#dotaclips"], ["dota"]),
    "eafc": ("EA Sports FC", ["#eafc", "#eafc26", "#fut", "#ultimateteam", "#football"], ["ea sports fc", "fifa"]),
    "nba2k": ("NBA 2K", ["#nba2k", "#nba2k26", "#2k", "#2kcommunity"], ["nba 2k"]),
    "roblox": ("Roblox", ["#roblox", "#robloxclips", "#robloxmemes", "#robloxedit"], ["roblox"]),
    "rust": ("Rust", ["#rust", "#playrust", "#rustgame", "#rustclips"], ["rust"]),
    "dbd": ("Dead by Daylight", ["#deadbydaylight", "#dbd", "#dbdclips"], ["dead by daylight"]),
    "eldenring": ("Elden Ring", ["#eldenring", "#fromsoftware", "#soulslike"], ["elden ring"]),
    "pokemon": ("Pokémon", ["#pokemon", "#pokemonclips", "#pokemoncommunity"], ["pokémon", "pokemon"]),
    "chess": ("Chess", ["#chess", "#chessclips", "#chesscom"], ["chess"]),
    "horror": ("Horror games", ["#horrorgaming", "#horrorgames", "#scary"], ["horror", "phasmophobia", "lethal company"]),
    "justchatting": ("Just Chatting / IRL", ["#justchatting", "#irl", "#streamermoments", "#funnymoments"], ["just chatting", "irl", "talk shows"]),
}

GENERIC = ["#twitch", "#twitchclips", "#streamer", "#gaming", "#fyp", "#viral"]


def choices() -> list[tuple[str, str]]:
    return [("auto", "Auto (what they're streaming)")] + [(k, v[0]) for k, v in GAMES.items()] + [("none", "No game hashtags")]


def match_category(category: str) -> str | None:
    """Twitch category name ('Grand Theft Auto V') -> our game key ('gta')."""
    c = (category or "").lower()
    if not c:
        return None
    for key, (_name, _tags, words) in GAMES.items():
        if any(w == c or re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", c) for w in words):
            return key
    return None


def slug_tag(name: str) -> str:
    s = re.sub(r"[^0-9a-z]+", "", (name or "").lower())
    return f"#{s}" if s else ""


def resolve(choice: str, category: str = "") -> tuple[str, list[str]]:
    """(game name to show, hashtags) for a stream's picker choice and its live Twitch category."""
    if choice == "none":
        return "", []
    key = choice if choice in GAMES else match_category(category)
    if key:
        return GAMES[key][0], list(GAMES[key][1])
    if category:
        tag = slug_tag(category)
        return category, [tag] if tag else []
    return "", []


def hashtags(channel: str, choice: str, category: str = "") -> str:
    _name, game_tags = resolve(choice, category)
    tags, seen = [], set()
    for t in game_tags[:5] + [slug_tag(channel)] + GENERIC:
        if t and t not in seen:
            seen.add(t)
            tags.append(t)
    return " ".join(tags)
