basket-stats
============

Post-match video analysis for Norwegian basketball (kamper.basket.no / NIF API).

Load a match by ID, point it at the game video (YouTube URL or local file),
sync the video with the game clock, and you get:

  * every scored basket / free throw / foul from the official feed, clickable ->
    the video jumps to 5 s before the event
  * per-player box score (points, FT/2P/3P, fouls) from the feed
  * one-key tagging while you watch for the stats the scorer does not record:
    offensive/defensive rebounds, assists, steals, turnovers, blocks, missed
    field goals, substitutions (-> minutes played)
  * combined box score (feed + your tags), CSV export
  * clip export around any moment (local/downloaded video, needs ffmpeg)

Planned next (see ROADMAP below): player/ball tracking, court calibration,
shot chart, tactics visualisation.


Install on Windows
------------------

  1. Install Python 3.11 or newer from https://www.python.org/downloads/
     (tick "Add python.exe to PATH" in the installer).
  2. Install ffmpeg: `winget install Gyan.FFmpeg` in a terminal
     (or download from https://www.gyan.dev/ffmpeg/builds/ and add bin\ to PATH).
  3. Download or `git clone` this repo.
  4. Double-click  run.bat
     First run creates a virtual environment (.venv) and installs dependencies.
     Then it starts the server and opens http://localhost:8765 in your browser.

Linux / macOS:  ./run.sh


Using it
--------

  1. Type the match ID (from the kamper.basket.no URL, e.g. 8439241) -> Load.
  2. Paste the YouTube URL or a local file path (D:\video\game.mp4) -> Set.
     YouTube plays in the embedded player straight away. Click "Download" to keep
     a local 1080p copy (needed for clip export and for the upcoming tracking).
  3. Sync: pause the video where Q1 starts (clock 10:00 -> we count elapsed, so
     type 0:00), press "Set anchor". Do the same at the end of the period (10:00).
     Repeat for each period. Extra anchors in between improve accuracy when the
     clock stopped for long (timeouts, injuries).
  4. Events list: click any row to jump there. Filter by team/player/type.
  5. Tagging: pick team (H / V keys) and player, then press
       O = offensive rebound   D = defensive rebound   A = assist
       S = steal   T = turnover   B = block   2 / 3 = missed 2/3-pointer
       I = sub in   U = sub out   Space = play/pause   arrows = +-2 s (shift: 10 s)
     Tags are saved instantly and shown in the Events list and Box score.
  6. Box score tab -> Export CSV.

All data is stored under data/ (projects/<matchId>.json, videos/, clips/).


Court tab (Phase 2: tracking)
-----------------------------

  Needs a local video (set a file path, or Download the YouTube video first).

  1. Calibration: open "Court calibration", press "Grab frame at current time",
     pick a landmark in the dropdown and click it in the frame. Do 4 or more
     spread over the court (the far corners, half-court line ends, free-throw
     lane corners, 3-pt/baseline intersections...). Save. The mean error in
     metres is shown; under 0.3 m is good.
  2. Tracking: "Run 60 s from here" for a quick test, "Run on whole video" for
     the full game (background job; progress shown). Data lands in data/tracks/.
  3. The 2D court follows the video: one dot per tracked player, ID label, ball
     in orange, optional 2 s trails.

  Speed: the default (yolo11s, 1280 px, every 2nd frame) runs ~5-10 fps on CPU,
  i.e. a 70 min game takes several hours. First run downloads the model (~20 MB).
  GPU: Ultralytics uses CUDA automatically on NVIDIA. On AMD (Windows) install
  torch-directml (`pip install torch-directml`) and set device via
  BASKET_STATS_DEVICE=dml (experimental), or accept CPU speed and run overnight.


Development
-----------

  .venv/bin/python -m pytest          unit + API tests (offline, fixtures)
  node test_ui.js                     browser E2E (server running, network)


ROADMAP
-------

  Phase 1  (this)   sync tool, event navigation, box score, tagging, clips
  Phase 2  (wip)    YOLO player/ball detection + tracking, court calibration,
                    2D court view. Next: team colours, track->player identity,
                    minutes on court, heatmaps
  Phase 3           shot chart: feed shots for time/player, tracked ball for location,
                    miss candidates proposed for confirmation
  Phase 4           tactics view: 2D court playback of possessions
