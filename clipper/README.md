# GhostClip: automatic Twitch clipper

GhostClip records up to 5 Twitch streams at once on your PC, reads their chats as they happen,
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
2. Type up to 5 channel names (or paste twitch.tv links) and press **Start** on each, or **Start all**.
   - You can press Start before the stream begins. GhostClip waits and starts recording
     the moment the streamer goes live. If the stream drops and comes back, it carries on.
   - GhostClip records the stream by itself, so you don't need Twitch open in your browser.
     Watching it there too is fine.
3. Clips appear in the dashboard and in your clips folder as they're made. Each clip comes with:
   - `name.mp4`: the finished clip
   - `name.txt`: a ready-to-paste post caption and hashtags, what was said, and what chat spammed
   - `clips_log.csv`: a list of every clip
4. Missed something chat didn't react to? Press **✂** on that stream's row to clip its last 40 seconds.
5. When a stream is over, press its **Stop** (or **Stop all**). GhostClip finishes any clip in progress, then
   deletes the recording. Only your clips are kept.

**Choosing your folder:** click **Change** next to "Clips" (or open **Settings**) and pick the
folder you made. The default is `Videos\GhostClip\Clips`.

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

Every chat message is scored: laughing words and emotes, hype words, emoji spam, all-caps, and
"clip it" requests (these count extra). Every second it adds up the last 8 seconds and compares
that with **this channel's normal chat level**, so it works for a 20-viewer stream and a
50,000-viewer one. Just a busy chat (giveaways, raids) isn't enough: a real share of the
messages have to be laughing or hyped. Chat always reacts a few seconds after the moment, so
the clip starts about 30 seconds *before* chat exploded and ends about 10 seconds after the peak.

## Settings worth knowing

| Setting | What it does |
| --- | --- |
| How easily to clip | Slide right for more clips, left for only the biggest moments. |
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
python -m unittest tests.test_hype      # detector tests
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
| `ghostclip/framing.py` | Finds the face cam (bundled YuNet face model, MIT licence) |
| `ghostclip/engine.py` | Runs one stream's session; clips from all streams are edited one at a time |
| `ghostclip/web.py`, `dashboard.html` | Local dashboard on 127.0.0.1 |
