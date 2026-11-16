import unittest

from ghostclip.captions import Word, build_ass
from ghostclip.profanity import censor_text, censor_word, is_swear, mute_filter, mute_spans


class ProfanityTest(unittest.TestCase):
    def test_swears_caught(self):
        for w in ["fuck", "Fucking!", "FUUUCK", "motherfucker,", "fuckin'", "shit", "bullshit.", "SHITTY",
                  "bitch", "bitches", "ass", "asshole", "dumbass", "pussy", "dick", "cunt", "bastard",
                  "nigga", "niggas", "retarded", "wtf", "f***", "s**t"]:
            self.assertTrue(is_swear(w), w)

    def test_normal_words_left_alone(self):
        for w in ["class", "pass", "grass", "assist", "assume", "bass", "glass", "hello", "shiny", "fun",
                  "cocktail", "peacock", "Niger", "night", "hell", "damn", "what", "classic", "harass",
                  "***", "5", ""]:
            self.assertFalse(is_swear(w), w)

    def test_censor_keeps_first_letter_and_punctuation(self):
        self.assertEqual(censor_word("Fucking!"), "F***!")
        self.assertEqual(censor_word("shit,"), "s***,")
        self.assertEqual(censor_word("hello"), "hello")
        self.assertEqual(censor_text("what the fuck bro, that's bullshit"), "what the f*** bro, that's b***")

    def test_extra_words(self):
        self.assertTrue(is_swear("Bozo!", ["bozo"]))
        self.assertEqual(censor_text("you bozo", ["bozo"]), "you b***")

    def test_captions_starred(self):
        words = [Word(0.0, 0.3, "what"), Word(0.3, 0.6, "the"), Word(0.6, 0.9, "fuck")]
        self.assertIn("F***", build_ass(words, "stack", 5, censor=[]))
        self.assertNotIn("FUCK", build_ass(words, "stack", 5, censor=[]))
        self.assertIn("FUCK", build_ass(words, "stack", 5))   # censoring off

    def test_mute_spans_padded_and_merged(self):
        words = [Word(1.0, 1.3, "fuck"), Word(1.35, 1.6, "shit"), Word(5.0, 5.4, "hello"), Word(9.9, 10.2, "bitch")]
        spans = mute_spans(words, duration=10.1)
        self.assertEqual(len(spans), 2)
        self.assertAlmostEqual(spans[0][0], 0.92)
        self.assertAlmostEqual(spans[0][1], 1.72)
        self.assertAlmostEqual(spans[1][1], 10.1)
        self.assertEqual(mute_filter([]), "")
        self.assertIn("volume=", mute_filter(spans))


if __name__ == "__main__":
    unittest.main()
