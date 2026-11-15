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
