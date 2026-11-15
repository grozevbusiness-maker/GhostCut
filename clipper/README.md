# GhostClip: automatic Twitch clipper

GhostClip records a Twitch stream on your PC, reads the chat as it happens, and when chat
goes wild (laughing, `LMFAO`, `KEKW`, 😂💀, `W W W`, "CLIP IT") it cuts that moment into a
30–90 second clip. It reframes the clip to vertical 9:16, adds word-by-word captions of what
the streamer says, and saves it to your clips folder, ready for TikTok, Shorts or Reels.

## Set up (once, on Windows)

1. Download this folder to your PC (on GitHub: **Code → Download ZIP**, then unzip it).
2. Double-click **`setup_windows.bat`** inside the `clipper` folder.
   It installs Python (if needed), streamlink (records the stream), ffmpeg (edits the video)
   and the caption engine, then puts a **GhostClip** icon on your desktop.
   If it says Python was just installed, run it a second time.

## Use it

1. Double-click **GhostClip** on your desktop. A black window opens (keep it open) and the
   dashboard opens in your browser.
2. Type the channel name (or paste the twitch.tv link) and press **Start recording**.
   - You can press Start before the stream begins. GhostClip waits and starts recording
     the moment the streamer goes live. If the stream drops and comes back, it carries on.
   - GhostClip records the stream by itself, so you don't need Twitch open in your browser.
     Watching it there too is fine.
3. Clips appear in the dashboard and in your clips folder as they're made. Each clip comes with:
   - `name.mp4`: the finished clip
   - `name.txt`: a ready-to-paste post caption and hashtags, what was said, and what chat spammed
   - `clips_log.csv`: a list of every clip
4. Missed something chat didn't react to? Press **✂ Clip the last 40s**.
5. When the stream is over, press **Stop**. GhostClip finishes any clip in progress and saves
   the whole stream as one `.mp4` in your recordings folder.

**Choosing your folder:** click **Change** next to "Clips" (or open **Settings**) and pick the
folder you made. The default is `Videos\GhostClip\Clips`.

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
| Clip format | Vertical with blurred background (default), vertical zoomed to the center, or original 16:9. |
| Seconds before / after | How much of the build-up and the reaction to keep. |
| Timing nudge | If clips consistently start a bit early or late, adjust it here (e.g. `3` or `-3`). |
| Keep the whole stream recording | Off = keep only the last 15 minutes, to save disk space (1080p60 is ~3 GB per hour). |
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
| `ghostclip/engine.py` | Runs one session and ties the parts together |
| `ghostclip/web.py`, `dashboard.html` | Local dashboard on 127.0.0.1 |
