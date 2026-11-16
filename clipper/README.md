# GhostClip: automatic Twitch clipper

GhostClip records up to 10 Twitch streams at once on your PC, reads their chats as they happen,
and when chat goes wild (laughing, `LMFAO`, `KEKW`, 😂💀, `W W W`, "CLIP IT") it cuts that moment
into a 30–90 second clip. It makes the clip fill a phone screen (streamer's face cam on top, the
middle of the stream below), adds word-by-word captions of what the streamer says, and saves it to
your clips folder, ready for TikTok, Shorts or Reels.

## Set up (once, on Windows)

1. Download this folder to your PC (on GitHub: **Code → Download ZIP**, then unzip it).
2. Double-click **`setup_windows.bat`** inside the `clipper` folder.
   It installs Python (if needed), streamlink (records the stream), ffmpeg (edits the video)
   and the caption engine, then puts a **GhostClip** icon on your desktop.
   If it says Python was just installed, run it a second time.

## Use it

1. Double-click **GhostClip** on your desktop. A black window opens (keep it open) and the
   dashboard opens in your browser.
2. In the **Add a stream** card, type a channel name (or paste a twitch.tv link) and press
   **Add & start**. Add up to 10. Each stream gets its own card with live chat, messages per
   minute and a hype chart. **Start all** / **Stop all** control every stream at once.
   - Pick the **game** on each card (or leave it on *Auto*, which reads it from Twitch). The
     game's popular hashtags are added to that stream's post captions.
   - You can press Start before the stream begins. GhostClip waits and starts recording
     the moment the streamer goes live. If the stream drops and comes back, it carries on.
   - GhostClip records the stream by itself, so you don't need Twitch open in your browser.
     Watching it there too is fine.
3. Clips appear in the dashboard and in your clips folder as they're made. Each clip comes with:
   - `name.mp4`: the finished clip
   - `name.txt`: a ready-to-paste post caption and hashtags, what was said, and what chat spammed
   - `clips_log.csv`: a list of every clip
4. Missed something chat didn't react to? Press **✂** on that stream's card to clip its last 40 seconds.
5. When a stream is over, press its **Stop** (or **Stop all**). GhostClip finishes any clip in progress, then
   deletes the recording. Only your clips are kept.

**Choosing your folder:** click **Change** next to "Clips" (or open **Settings**) and pick the
folder you made. The default is `Videos\GhostClip\Clips`.

## Posting to Instagram

1. Click **Instagram** (top right), then **Open Instagram window**. A Chrome window opens.
2. Log in to the account you post on. You only do this once; it stays logged in.
3. Press **POST TO INSTAGRAM** under a clip. GhostClip posts it as a Reel with its caption and
   hashtags in that window, the way you would by hand, and marks the clip **✓ POSTED**.

It works with Instagram in English, Bulgarian and other common languages. If Instagram shows
something unexpected, GhostClip stops at that step and tells you, and saves a screenshot of the
window in `%APPDATA%\GhostClip\instagram_debug` (send it over and the next version can handle it). Finish the
post in the window yourself (the caption is already copied, just paste it). Keep it to a few posts
a day: lots of automatic posts in a row can make Instagram suspicious of the account.

### Posting by itself

In the Instagram panel, switch on **Post the best clips by themselves**. From then on GhostClip
posts the strongest new clip whenever the limits allow, with nobody at the PC:

- **Most posts a day** (default 5) counts every post in the last 24 hours, by hand or automatic.
- **Minutes between** (default 60) is the shortest gap between two posts.
- **Only moments above**: *Strong* posts only clips where chat went well past the clip line.
  A clip you made yourself with ✂ always counts as strong.
- Only clips made *after* you switch it on are posted (never the old backlog), each clip at
  most once, and a moment older than 6 hours is skipped as stale.
- After 2 failed posts in a row it pauses itself and says why. Fix the Instagram window, then
  switch it off and on.

GhostClip has to keep running with your streams started (press **Start**; it waits for streamers
to go live). The Instagram window opens by itself when a post is due, and the PC is kept awake
while streams run or auto-posting is on. A green **Auto-post 2/5** chip at the top shows it's on.

## Analytics

Open the **Analytics** tab, type your TikTok and YouTube names (Instagram uses the account
logged in to the Instagram window), and press **Refresh stats**. You get total views, posts and
followers across all three, views per platform, your top posts, and a chart of how your views grow
(one point per day you refresh). TikTok and YouTube are read from your public profile pages in
GhostClip's browser window. For exact YouTube numbers, add a free YouTube Data API key
(Google Cloud Console → APIs & Services → enable "YouTube Data API v3" → Credentials → API key).

## Other handy bits

- While a clip is being made, a **Making a clip** badge with a blinking red dot shows at the top, the stream's
  card says **CLIPPING**, and the browser tab title starts with 🔴.

## Updating

Close GhostClip, then double-click **`update.bat`**. It downloads the newest version and
keeps your settings.

## The clip layout

GhostClip finds the streamer's face cam by itself (it looks for a face that stays in one place
away from the middle of the screen) and puts it on top, with the middle of the stream below and
captions at the bottom. If a streamer has no face cam, or is full-screen on camera, the clip is
zoomed into the middle instead. If it picks the wrong spot for a streamer, set **Where is the
face cam?** in Settings to the right corner.

## How it picks moments

Every chat message is scored. **Laughing** (LMAO, LOL, KEKW, ICANT, 😂💀😭, channel emotes like
kaicLUL), **shock** (NAHHH, HUH, WTF, OMG, NOOOO, ????) and **"clip it"** requests start a moment.
**Celebrating** (LETS GOOO, POG, 🔥) only adds to one, and **W / L / GG** walls (sub gifts, raids)
barely count. Every second it adds up the last 8 seconds and compares that with **this channel's
normal chat level**, so it works for a 20-viewer stream and a 100,000-viewer one.

It's built for giant chats on big days (a GTA 6 launch on Kai Cenat's stream) where chat never stops:

- Each chatter counts once per 8 seconds, so one person's emote wall isn't a moment.
- What a chat spams *all the time* (W, "GTA 6", "get sturdy", 🔥) fades out. A word only counts
  fully when it surges above its own normal level for that channel.
- Shock words count in short messages only ("NAHHH"), not in sentences ("nah he's bad").
- A moment must peak clearly above the clip line, and every clip made in the last hour raises
  the bar (an hourly budget set by the slider). Twice the budget is a hard ceiling.

Chat always reacts a few seconds after the moment, so the clip starts about 30 seconds *before*
chat exploded and ends about 10 seconds after the peak.

## Settings worth knowing

| Setting | What it does |
| --- | --- |
| How easily to clip | Slide right for more clips, left for only the biggest moments. It shows about how many clips an hour you'll get on a busy stream: roughly 1, 3, 5, 12 and 18 from left to right (measured on real chat from Kai Cenat's GTA marathon, Fanum, Jason and Stable Ronaldo). Calm streams make fewer. |
| Star out / mute swear words | On by default. Captions and post text show `F***` instead of swear words and slurs, and the audio goes silent for each one (about half a second). Instagram recommends clips with swearing less. Add your own words under *More words to censor*. Clips made before this was on keep their swearing. |
| Clip format | Face cam on top + gameplay (default), zoomed to the center only, whole picture on a blurred background, or original 16:9. |
| Where is the face cam? | Automatic, or pick a corner if auto gets it wrong. |
| Seconds before / after | How much of the build-up and the reaction to keep. |
| Timing nudge | If clips consistently start a bit early or late, adjust it here (e.g. `3` or `-3`). |
| Also save the whole stream | Off by default: only the last 15 minutes are kept while recording, and it's all deleted on Stop. Turn on to keep the full stream as one `.mp4`. |
| Caption accuracy / language | `small` is a good balance. Set the language if auto-detect guesses wrong. |
| Laughing / hype words | Add the channel's own emotes (e.g. its sub emotes) to make detection sharper. |
| Twitch token | Optional. With Turbo or a sub to the channel, ads are removed from the recording. |

With an NVIDIA graphics card, videos are encoded on the GPU automatically. Captions run
on the CPU unless your PC has NVIDIA's CUDA 12 libraries installed.

## Troubleshooting

- **"Waiting for … to go live" but the streamer is live:** check the channel name spelling.
- **Clips start too early or too late:** use *Timing nudge* in Settings.
- **No captions:** the activity log says why. Re-running `setup_windows.bat` fixes missing parts.
- **Everything is logged** to `%APPDATA%\GhostClip\ghostclip.log`.
- Don't close the black window while recording. Use **Stop** or **Quit app**, so the last
  clip and the full recording are saved properly.

Only clip and post streams you're allowed to. Many streamers welcome clippers; some don't.

## For developers

```
pip install -r requirements.txt        # plus ffmpeg on PATH
python -m ghostclip                     # dashboard
python -m ghostclip --channel name      # start right away
python -m unittest tests.test_hype tests.test_autopost tests.test_profanity   # unit tests
python tests/e2e_sim.py                 # full pipeline with a fake stream and fake chat
python tests/demo_dashboard.py          # dashboard with a fake stream and fake chat
```

| File | Role |
| --- | --- |
| `ghostclip/chat.py` | Reads Twitch chat anonymously over IRC |
| `ghostclip/hype.py` | Scores messages and finds hype moments |
| `ghostclip/recorder.py` | streamlink → ffmpeg 10-second pieces, maps chat time to video time |
| `ghostclip/editor.py` | Cuts, reframes, normalizes audio, burns in captions |
| `ghostclip/captions.py` | faster-whisper word timings → styled ASS captions |
| `ghostclip/profanity.py` | Swear words: stars in captions and post text, where to mute the audio |
| `ghostclip/framing.py` | Finds the face cam (bundled YuNet face model, MIT licence) |
| `ghostclip/games.py` | Game list and hashtags |
| `ghostclip/instagram.py` | The app's Chrome/Edge window: Instagram posting, runs stats jobs |
| `ghostclip/autopost.py` | Posts the best new clips by itself, within the daily limit and gap |
| `ghostclip/analytics.py` | Reads Instagram, TikTok and YouTube stats for the Analytics tab |
| `ghostclip/engine.py` | Runs one stream's session; clips from all streams are edited one at a time |
| `ghostclip/web.py`, `dashboard.html` | Local dashboard on 127.0.0.1 |
