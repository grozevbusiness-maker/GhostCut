"""Auto-posting picks the right clips and respects its limits (fake Instagram, temp folder)."""
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from ghostclip.autopost import AutoPoster
from ghostclip.settings import Settings


class NoSave(Settings):
    def save(self):   # never touch the real settings file
        pass


class FakeIG:
    def __init__(self, result=True, status="Posted ✓"):
        self.result, self.status_text, self.posted = result, status, []
        self.busy = False

    @staticmethod
    def available():
        return True

    def post(self, clip, caption, done=None):
        self.posted.append(clip.name)
        if self.result:   # what instagram.py does on success
            meta = clip.with_suffix(".json")
            info = json.loads(meta.read_text(encoding="utf-8"))
            info["instagram"] = time.strftime("%Y-%m-%d %H:%M")
            meta.write_text(json.dumps(info), encoding="utf-8")
        done(self.result, self.status_text if not self.result else "Posted ✓")


def make_clip(d: Path, name: str, hype: float, kind="laugh", age=0.0, **extra):
    clip = d / f"{name}.mp4"
    clip.write_bytes(b"x")
    info = {"file": clip.name, "kind": kind, "hype": hype, "post": f"caption for {name}", **extra}
    clip.with_suffix(".json").write_text(json.dumps(info), encoding="utf-8")
    t = time.time() - age
    os.utime(clip, (t, t))
    return clip


class TestAutoPost(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="gc_autopost_"))
        self.s = NoSave(clips_dir=str(self.dir), auto_post=False, auto_post_per_day=3, auto_post_gap=60,
                        auto_post_min_hype=1.6)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def poster(self, ig):
        return AutoPoster(self.s, ig, lambda m: None, lambda: False, start=False)   # driven by hand here

    def switch_on(self, ap):
        self.s.auto_post = True
        ap.settings_changed()

    def test_posts_strongest_new_clip_only(self):
        make_clip(self.dir, "old_backlog", 5.0, age=600)       # made before auto-posting was on
        ig = FakeIG()
        ap = self.poster(ig)
        self.switch_on(ap)
        make_clip(self.dir, "weak", 1.2)                       # under the hype bar
        make_clip(self.dir, "good", 2.0)
        make_clip(self.dir, "best", 3.4)
        ap._step()
        self.assertEqual(ig.posted, ["best.mp4"])
        ap._step()                                             # the gap blocks a second post
        self.assertEqual(ig.posted, ["best.mp4"])
        info = json.loads((self.dir / "best.json").read_text(encoding="utf-8"))
        self.assertEqual(info["auto_post"], "posted")
        self.assertEqual(ap.state()["today"], 1)

    def test_daily_limit_counts_manual_posts(self):
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 3 * 3600))
        for i in range(3):
            make_clip(self.dir, f"posted{i}", 2.0, instagram=stamp, age=4 * 3600)
        ig = FakeIG()
        ap = self.poster(ig)
        self.switch_on(ap)
        make_clip(self.dir, "new", 3.0)
        ap._step()
        self.assertEqual(ig.posted, [])                        # 3 of 3 already today

    def test_pauses_after_failures_and_never_retries_a_clip(self):
        ig = FakeIG(result=False, status="Stopped at the Next button. Finish it in the Instagram window.")
        self.s.auto_post_gap = 0
        ap = self.poster(ig)
        self.switch_on(ap)
        make_clip(self.dir, "a", 3.0)
        make_clip(self.dir, "b", 2.5)
        make_clip(self.dir, "c", 2.0)
        for _ in range(5):
            ap._step()
        self.assertEqual(ig.posted, ["a.mp4", "b.mp4"])        # c never tried: paused after 2 failures
        self.assertTrue(ap.paused)
        self.s.auto_post = False
        ap.settings_changed()
        self.switch_on(ap)                                     # off and on again clears the pause
        self.assertEqual(ap.paused, "")

    def test_manual_clip_counts_as_strong(self):
        ig = FakeIG()
        ap = self.poster(ig)
        self.switch_on(ap)
        make_clip(self.dir, "auto", 2.5)
        make_clip(self.dir, "by_hand", 0, kind="manual")
        ap._step()
        self.assertEqual(ig.posted, ["by_hand.mp4"])

    def test_off_posts_nothing(self):
        ig = FakeIG()
        ap = self.poster(ig)
        make_clip(self.dir, "x", 5.0)
        ap._step()
        self.assertEqual(ig.posted, [])


if __name__ == "__main__":
    unittest.main()
