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
     lane corners, 3-pt/baseline intersections...). With 7+ points the lens
     (fisheye) distortion is fitted as well - include points near the image
     edges for that. Save. The error in metres is shown; under 0.3 m is good.
     The green outline drawn over the frame shows where the model thinks the
     lines are; mouse wheel zooms, drag pans.
  2. Tracking: "Run 60 s from here" for a quick test, "Run on whole video" for
     the full game (background job; progress shown). Data lands in data/tracks/.
  3. The 2D court follows the video: one dot per tracked player, ID label, ball
     in orange, optional 2 s trails.

  Speed: the default (yolo11s, 1280 px, every 2nd frame) runs ~5-10 fps on CPU,
  i.e. a 70 min game takes several hours. First run downloads the model (~20 MB).
  GPU: Ultralytics uses CUDA automatically on NVIDIA. On AMD (Windows) install
  torch-directml (`pip install torch-directml`) and set device via
  BASKET_STATS_DEVICE=dml (experimental), or accept CPU speed and run overnight.


Shots tab (Phase 3: shot chart)
-------------------------------

  Needs the court calibration (Court tab) and synced periods.

  Every shot attempt is listed: feed shots (made/missed 2P/3P/FT) plus the
  2/3-pt misses you tagged. Free throws are placed automatically; field goals
  need one click each:

  1. Click a shot in the list (video jumps 5 s before it), then press L or its
     "locate" button. The frame at the shot opens fullscreen (1.5 s before the
     scorer's clock, since the scorer presses the button after the release;
     use the -0.5s/+0.5s buttons to step to the release). Click where the
     shooter's FEET are. The point is converted to court metres, the zone
     (paint / mid / 3pt) and distance are shown, and the next unlocated shot
     opens automatically. "Next unlocated" / Skip move on without placing.
     With tracking data (Court tab, "Run on whole video") the frame also shows
     every tracked player as a box: click a box to use that player's feet
     instead of aiming. If the ball was detected, the app finds the moment it
     left a player's hands, jumps the frame there and marks that player with
     a yellow star - press Enter to accept, or click someone else.
     The numbers on the boxes are tracker ids (arbitrary; a player gets a new
     one every time the tracker loses him). Each time you confirm a box as the
     shooter, the app learns "this track = this player" (the feed names the
     shooter), and from then on that box - and its dot on the Court tab - shows
     the shirt number and name, and is proposed automatically when that player
     shoots again. Re-running tracking renumbers the tracks and forgets these.
  2. Chart: half court, basket at the top. Green dot = made, red cross = missed,
     orange = the location disagrees with the feed (a "2" beyond the arc or a
     "3" inside it) - check the click or the scorer. Drag a marker to correct
     it, right-click to remove it. Filter by team, player, period.
  3. Zone summary above the list: made/attempts (%) for paint, mid-range,
     3-pt and FT.

  Ends: the chart needs to know which basket each team attacks. It is inferred
  from the first located field goal (home attacks L or R basket in the 1st
  half; teams swap at half time, overtime keeps 2nd-half ends). Override it
  with the "Home attacks 1st half" dropdown if a heave from the far half
  confused the guess.

  Locations are stored in the project file under "shots" (keyed by the feed
  event id, or t<tagId> for tagged misses), in full-court metres.


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
  Phase 3  (this)   shot chart: feed shots + tagged misses, located by one click on the frame;
                    next: tracked ball/player position proposed automatically,
                    miss candidates proposed for confirmation
  Phase 4           tactics view: 2D court playback of possessions
