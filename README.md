# showroom-player

A desktop GUI player for watching [SHOWROOM](https://www.showroom-live.com) live streams.

## Features

- Open a SHOWROOM room by URL or room key and start playback directly in the app
- Expand HLS adaptive streams into individual quality options with bitrate, resolution, and frame rate
- Browse live rooms, switch stream quality, and watch comments in real time
- Log in from the embedded browser to send comments while watching

## Requirements

- Python 3
- Qt WebEngine support through `PySide6`

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python main.py
```

## Packaging

### macOS

Do not use `--onefile` on macOS. Building an `.app` bundle starts faster and can be moved into `/Applications` directly.

```bash
source .venv/bin/activate
pip install pyinstaller
pyinstaller --windowed --name showroom-player main.py
ditto -c -k --sequesterRsrc --keepParent dist/showroom-player.app dist/showroom-player_macos_arm64.app.zip
```

The generated app bundle is `dist/showroom-player.app`. The release artifact should be `dist/showroom-player_macos_arm64.app.zip`.

Enter a room URL or key, e.g.:

- `https://www.showroom-live.com/r/ROOM_KEY`
- `ROOM_KEY`

Click **Open** to start playback. You can then switch stream quality from the player controls, browse live rooms on the left, and join the chat after signing in.
