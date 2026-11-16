import random
import unittest

from ghostclip.chat import ChatMessage, parse_privmsg
from ghostclip.hype import HypeDetector, MessageScorer
from ghostclip.settings import Settings

CALM = ["hi chat", "what game is this", "gg", "lets see", "nice", "true", "hello from brazil",
        "how long is stream", "no", "what", "ok", "he is so bad at this", "chat is he cooked"]
LAUGH = ["KEKW KEKW KEKW", "LMFAO", "😂😂😂", "OMEGALUL", "im dead 💀", "LMAOOO", "hahahaha", "ICANT", "LUL"]


def simulate(per_sec_calm, bursts, seconds=400, burst_rate=None, seed=1, settings=None):
    """bursts: list of (start_second, length). Returns the events found."""
    rnd = random.Random(seed)
    s = settings or Settings()
    d = HypeDetector(s)
    events = []
    for t in range(seconds):
        in_burst = any(b <= t < b + n for b, n in bursts)
        rate = burst_rate or per_sec_calm * 6 + 3
        n = rnd.randint(0, 2) + (rate if in_burst else per_sec_calm)
        for _ in range(int(n * rnd.uniform(0.6, 1.2))):
            text = rnd.choice(LAUGH) if in_burst and rnd.random() < 0.75 else rnd.choice(CALM)
            d.add(ChatMessage(t + rnd.random(), f"u{rnd.randint(1, 5000)}", text))
        ev = d.tick(t + 1.0)
        if ev:
            events.append(ev)
    return events


class TestScorer(unittest.TestCase):
    def test_words(self):
        s = Settings()
        sc = MessageScorer(s.laugh_words, s.hype_words, s.clip_phrases)
        self.assertEqual(sc.score("hi chat how are you")[0], 0)
        self.assertEqual(sc.score("no")[0], 0)
        self.assertEqual(sc.score("what")[0], 0)
        self.assertGreater(sc.score("LMFAO")[0], 0)
        self.assertGreater(sc.score("hahahaha")[0], 0)
        self.assertGreater(sc.score("😂")[0], 0)
        self.assertEqual(sc.score("KEKW KEKW KEKW")[1], "laugh")
        self.assertEqual(sc.score("CLIP IT")[1], "clip")
        self.assertGreater(sc.score("CLIP IT")[0], sc.score("LMAO")[0])
        self.assertGreater(sc.score("W W W")[0], 0)
        self.assertGreater(sc.score("?????")[0], 0)

    def test_big_streamer_chat(self):
        """Lines as they appear in Kai Cenat / Jason / Fanum chats (from real chat replays)."""
        s = Settings()
        sc = MessageScorer(s.laugh_words, s.hype_words, s.clip_phrases)
        for text in ("LMAOOOOOOOOOOOO", "LOOOL", "JinxLUL", "kaicLUL kaicLUL", "NAHHHHH", "HUH", "DAMNNNN",
                     "WTF", "OMG", "?????", "NOOOOO", "YOOOOO", "😭😭😭", "sob"):
            w, _kind, _keys = sc.score_keys(text)
            self.assertGreaterEqual(w, 1.0, text)
        # everyday chat isn't a reaction
        for text in ("no", "yo", "bro", "nah he is bad at this", "damn i have work tomorrow",
                     "huh what did i miss guys", "some clip went viral from the last stream"):
            self.assertEqual(sc.score_keys(text)[0], 0, text)
        # W / L / GG walls (sub gifts, raids) and celebrating are weak: they can't start a moment
        for text in ("W", "WWWWWW", "W 10", "GG", "L"):
            self.assertLess(sc.score_keys(text)[0], 0.3, text)
        for text in ("LETS GOOOO", "POGGERS", "🔥🔥🔥"):
            self.assertEqual(sc.score_keys(text)[1], "celebrate", text)

    def test_launch_day_spam_does_not_clip(self):
        """GTA 6 launch: chat spams W / LETS GO / 🔥 nonstop, in waves. No laughing, no shock."""
        rnd = random.Random(5)
        spam = ["GTA 6 W", "W", "WWWW", "LETS GOOOO", "🔥🔥🔥", "GTA 6 LETS GO", "W W W", "VICE CITY W", "W KAI"]
        d = HypeDetector(Settings())
        for t in range(900):
            wave = 4 if t % 240 < 40 else 1
            for _ in range(rnd.randint(3, 8) * wave):
                d.add(ChatMessage(t + rnd.random(), f"u{rnd.randint(1, 30000)}", rnd.choice(spam + CALM)))
            self.assertIsNone(d.tick(t + 1.0), t)

    def test_laugh_through_launch_day_spam(self):
        """...but a real laugh in the middle of that spam still gets clipped."""
        rnd = random.Random(6)
        spam = ["GTA 6 W", "W", "WWWW", "LETS GOOOO", "🔥🔥🔥", "W W W"]
        d = HypeDetector(Settings())
        events = []
        for t in range(400):
            for _ in range(rnd.randint(4, 8)):
                d.add(ChatMessage(t + rnd.random(), f"u{rnd.randint(1, 30000)}", rnd.choice(spam + CALM)))
            if 300 <= t < 312:
                for _ in range(rnd.randint(14, 22)):
                    d.add(ChatMessage(t + rnd.random(), f"u{rnd.randint(1, 30000)}", rnd.choice(LAUGH)))
            ev = d.tick(t + 1.0)
            if ev:
                events.append(ev)
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0].kind, "laugh")

    def test_hourly_budget(self):
        """A wild stream with a big laugh every 2 minutes: clips slow down as the hour fills up."""
        s = Settings()
        busy = simulate(per_sec_calm=4, bursts=[(120 * i + 60, 10) for i in range(30)], seconds=3600, settings=s)
        self.assertLessEqual(len(busy), 12, len(busy))
        self.assertGreaterEqual(len(busy), 3, len(busy))

    def test_irc_parse(self):
        line = ("@badge-info=;display-name=Some_User;emotes=25:0-4,6-10;id=x :some_user!some_user@some_user"
                ".tmi.twitch.tv PRIVMSG #chan :Kappa Kappa lol")
        self.assertEqual(parse_privmsg(line), ("Some_User", "Kappa Kappa lol", 2))
        self.assertIsNone(parse_privmsg(":tmi.twitch.tv 001 justinfan1 :Welcome"))


class TestDetector(unittest.TestCase):
    def test_small_channel_one_moment(self):
        ev = simulate(per_sec_calm=0.3, bursts=[(200, 12)], burst_rate=2)
        self.assertEqual(len(ev), 1, ev)
        self.assertTrue(198 <= ev[0].start <= 210, ev[0])
        self.assertEqual(ev[0].kind, "laugh")

    def test_big_channel_two_moments(self):
        ev = simulate(per_sec_calm=8, bursts=[(150, 15), (300, 10)])
        self.assertEqual(len(ev), 2, ev)

    def test_calm_chat_no_clips(self):
        for rate in (0.3, 3, 15):
            self.assertEqual(simulate(per_sec_calm=rate, bursts=[], seed=rate * 10), [], rate)

    def test_busy_but_not_funny(self):
        # a chat spike without laughing (e.g. a giveaway) shouldn't clip
        s = Settings()
        d = HypeDetector(s)
        for t in range(300):
            n = 40 if 150 <= t < 170 else 3
            for i in range(n):
                d.add(ChatMessage(t + i / n, f"u{i}", "hello there friend"))
            self.assertIsNone(d.tick(t + 1.0))

    def test_cooldown_merges_back_to_back(self):
        ev = simulate(per_sec_calm=2, bursts=[(150, 8), (165, 8)])
        self.assertEqual(len(ev), 1, ev)


if __name__ == "__main__":
    unittest.main()
