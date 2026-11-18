import unittest

from ghostclip.editor import post_text
from ghostclip.platforms import final_caption, prepare, with_credit, youtube_title
from ghostclip.settings import Settings


class PlatformTextTest(unittest.TestCase):
    def test_new_captions_credit_the_streamer(self):
        text = post_text("kaicenat", "laugh", "", "#kaicenat #gta6")
        self.assertIn("🎥 Credit: twitch.tv/kaicenat", text)
        self.assertTrue(text.rstrip().endswith("#gta6"))       # credit goes above the hashtags

    def test_old_captions_get_credit_once(self):
        old = "kaicenat had chat crying 😂\n\n#kaicenat #gta6"
        t = with_credit(old, "kaicenat")
        self.assertEqual(t.count("twitch.tv/kaicenat"), 1)
        self.assertEqual(t.split("\n\n")[1], "🎥 Credit: twitch.tv/kaicenat")
        self.assertEqual(with_credit(t, "kaicenat"), t)

    def test_youtube_links_are_clickable(self):
        t = final_caption("hi\n\n🎥 Credit: twitch.tv/camy\n\n#camy", "camy", "youtube")
        self.assertIn("https://www.twitch.tv/camy", t)
        self.assertNotIn("https://www.https", t)

    def test_swearing_starred_but_link_untouched(self):
        t = final_caption('"what the fuck bro" 💀\n\n#fyp', "shitposter", "tiktok", censor=[])
        self.assertIn("f***", t)
        self.assertNotIn("fuck", t)
        self.assertIn("twitch.tv/shitposter", t)

    def test_youtube_title(self):
        title = youtube_title('"NAH this is fucking crazy" 💀\n\n🎥 Credit: twitch.tv/x\n\n#gta6', "x", censor=[])
        self.assertEqual(title, '"NAH this is f*** crazy" 💀')
        self.assertEqual(youtube_title("#a #b", "kai"), "kai moment")
        self.assertLessEqual(len(youtube_title("word " * 60, "x")), 100)

    def test_prepare_follows_settings(self):
        s = Settings(censor_swears=False)
        text, _ = prepare({"channel": "c", "post": "holy shit"}, "instagram", s)
        self.assertIn("shit", text)
        s.censor_swears = True
        text, _ = prepare({"channel": "c", "post": "holy shit"}, "instagram", s)
        self.assertIn("s***", text)


if __name__ == "__main__":
    unittest.main()
