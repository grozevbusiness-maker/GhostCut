# Ghostcut

Ghostcut is a faceless reel studio: pick a genre, a look and a voice, and get a short vertical reel with captions.

This repository holds the **test version**, a single-page prototype served by GitHub Pages from `index.html`.

What the test version does and doesn't do:

- Genre reels are ready-made samples drawn in the browser. The "Your own idea" box uses templates here (it only writes with AI when the page runs inside Claude).
- Voices come from the visitor's browser.
- Accounts and the reel library are saved only in each visitor's own browser. Nothing is sent to a server.
- Checkout is test mode only. Nobody can pay and nothing is charged.
- The Business "Get in touch" form does not send anywhere yet.

## GhostClip (Twitch auto-clipper)

The [`clipper/`](clipper/) folder holds **GhostClip**, a separate Windows app that records a Twitch stream, reads chat, and saves captioned vertical clips of the moments chat goes wild for. See [clipper/README.md](clipper/README.md) to set it up.
