<p align="center">
  <img src="KaraokeBirdLogo.png" width="200" alt="KaraokeBird Logo" />
</p>

<h1 align="center">
  KaraokeBird
</h1>

<p align="center">
  The lightweight, transparent lyrics overlay for Windows.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows-blue" alt="Platform Windows">
  <img src="https://img.shields.io/badge/python-3.11+-yellow" alt="Python Version">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
</p>

**KaraokeBird** is a minimal, open-source lyrics visualizer that lives on your desktop. It detects what you are playing on Spotify (or any media player) and displays time-synced lyrics in a beautiful, transparent overlay that sits on top of your windows—letting you sing along while you work, game, or browse.

# 🚀 Features

KaraokeBird is designed to be seamless and unobtrusive.

🎤 **Real-Time Sync:**: Automatically fetches `.lrc` files to display lyrics exactly when they are sung.

👻 **Zero-Interruption Overlay**: The window uses a click-through window and is fully transparent to input. You can click, type, and interact with windows behind the lyrics as if they weren't there.

🎧 **Privacy-First & Portable**: No account creation, no API keys, and no data collection. Works with **Spotify**, **YouTube Music**, **Apple Music**, and browser media players via Windows Media Controls.

🎨 **Fully Customizable**:
*   **Typography**: Custom fonts, stroke weights, and highlight colors.
*   **Layout**: Drag the lyrics and track info straight into place on screen (tray → **Edit Position**), or fine-tune them with sliders.
*   **Animations**: Enable gentle fade, slide, or zoom transitions.
*   **Context Lines**: Choose to see previous/next lines or keep it minimal with just the current line.
*   **Readability Background**: An optional translucent box behind the text for bright windows.
*   **Style Presets**: Save a look (fonts, colors, stroke, animation, background) and switch back to it later.

🎶 **Lyrics Extras** (opt-in):
*   **Word-by-word highlighting** when the lyrics provider has word timing.
*   **Translation line** under the current lyric, in the language you choose.
*   **Per-song sync offset**, remembered for each song, on top of the global offset.
*   **Wrong lyrics? Search again** tries the next lyrics provider.

😴 **Stays Out of the Way**: Fades out 5 seconds after playback pauses or stops, and comes back when the music does.

⚡ **Instant Setup**: No logins, no API keys, and no complex configuration required. Just run and sing.

# 🎥 Demo

<p align="center">
  <video controls width="720" playsinline>
    <source src="https://raw.githubusercontent.com/joshshiman/karaokebird/main/GETKARAOKEBIRD%20%281%29.mp4" type="video/mp4">
    Your browser does not support the video tag. You can watch the demo directly here: <a href="https://raw.githubusercontent.com/joshshiman/karaokebird/main/GETKARAOKEBIRD%20%281%29.mp4">direct link</a>.
  </video>
</p>

See it in action: [Watch the demo on X / Twitter](https://twitter.com/joshshiman/status/2011283617134329988)

# 💻 Installation & Usage

### Prerequisites
*   Windows 10 or 11
*   Python 3.11 or higher

### Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/karaokebird.git
    cd karaokebird
    ```

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Run the app:**
    ```bash
    python main.py
    ```

4.  **Play music!** Start a song on Spotify or your preferred player. The lyrics will appear automatically.

# ⚙️ Configuration

Look for the **KaraokeBird icon** (green square or bird logo) in your system tray (near the clock).

*   **Left-click** the icon to show or hide the lyrics. The tooltip shows the current song.
*   **Right-click** it for the menu:
    *   **Edit Position**: makes the lyrics and track info draggable. Drag them anywhere (including onto another monitor), then press **Enter**/**Esc** or click **Done**. Settings → Layout → **Drag on screen…** does the same.
    *   **Sync offset**: if lyrics appear too early or too late, nudge them by 100 ms, either for **all songs** or just **this song** (remembered per song). The same global offset is in Settings → System.
    *   **Wrong lyrics? Search again**: tries the next lyrics provider (Lrclib, Musixmatch, NetEase, Megalobiz) and remembers the new result.
    *   **Source**: which player to follow. **Auto** follows whatever is playing (so a YouTube tab wins over a paused Spotify); pick an app to stick to it while it's open.
    *   **Settings...**: the full configuration window.
*   KaraokeBird remembers whether the overlay and track info were shown, and only one copy runs at a time.

### Settings

*   **Appearance**: fonts, colors, stroke, transitions (**Preview animation** plays the selected one), the readability background and its color/opacity, and **Style Presets** (Save…/Delete; presets are saved immediately).
*   **Layout**: display, position, number of previous/upcoming lines, track info position.
*   **System**:
    *   Global sync offset.
    *   **Fade out when nothing is playing** (on by default).
    *   **Start with Windows**: adds or removes a `KaraokeBird` entry under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.
    *   **Check for updates once a day** (on by default).
    *   **Lyrics**: **Highlight word by word**, **Translation** language, and **Clear lyrics cache**.
    *   **Hotkeys** for showing/hiding the lyrics and the track info, and for nudging this song's offset by ±100 ms.

Word-by-word timing and translations come only from Musixmatch, which is not always reachable. When they aren't available, KaraokeBird falls back to line-by-line lyrics with no translation.

All settings live in `%APPDATA%\KaraokeBird\settings.json`.

# 🛠️ How it Works

Unlike traditional lyrics apps that require a dedicated window, KaraokeBird uses the WinSDK to "listen" to your system's media bus. It then uses the syncedlyrics engine to scrape the most accurate timing data available, rendering it via a high-performance PyQt6 transparent layer.

*   **[PyQt6](https://pypi.org/project/PyQt6/)**: For the robust, transparent GUI and overlay capabilities.
*   **[winsdk](https://pypi.org/project/winsdk/)**: To interface directly with Windows Media Controls for metadata and timeline tracking.
*   **[syncedlyrics](https://github.com/moehuri/syncedlyrics)**: To scour the web for accurate time-synced lyrics.

# 🔒 Is KaraokeBird Safe?

Yes, and you don't have to take our word for it — the whole point of open source is that you can check yourself:

*   **Read the code.** The entire app is about 4,000 lines across `main.py`, `settings_ui.py`, `ui_components.py`, `lyrics.py`, `storage.py`, `updates.py`, and `autostart.py`. You can audit all of it in an afternoon.
*   **No network exfiltration.** Search the source for `requests`, `socket`, `urllib`, or `http`. The only outbound traffic is:
    1.  [`syncedlyrics`](https://github.com/moehuri/syncedlyrics), an open-source package that looks up lyrics text. It sends the song title and artist as the search term.
    2.  **The daily update check** (`updates.py`): at most once a day, an anonymous `GET https://api.github.com/repos/joshshiman/karaokebird/releases/latest` that reads the latest version number. Nothing about you or your music is sent. Turn it off in Settings → System → **Check for updates once a day**.
    3.  `webbrowser.open()` calls that open the GitHub Releases page when *you* click **Check for Updates...** or **Update available**.

    Nothing else leaves your machine.
*   **No accounts, no API keys, no telemetry.** Everything stays local, in `%APPDATA%\KaraokeBird\`:
    *   `settings.json`: your settings and style presets.
    *   `track_offsets.json`: per-song sync offsets.
    *   `lyrics_cache\`: one file per song with the lyrics already found. Songs you've played before load without network access. "Not found" results expire after 7 days. Settings → System → **Clear lyrics cache** deletes the folder.
    *   `karaokebird.log`: a rotating log file.

    Nothing is uploaded anywhere.
*   **Start with Windows** only writes the single `KaraokeBird` value under your user's `Run` registry key, and only when you tick the box; unticking removes it.
*   **No AI/LLM involved.** KaraokeBird doesn't call any AI model or service today. If that ever changes in a future release, this section will be updated to say exactly what data would be sent and how to inspect the prompts/tool calls before you install it.
*   **Run from source, not a prebuilt binary.** The safest way to use KaraokeBird is `pip install -r requirements.txt && python main.py` from a clone of this repo — that way you're only ever running code you (or GitHub) can read. If a packaged `.exe` is ever published, treat it like any downloaded binary: scan it with [VirusTotal](https://www.virustotal.com/) before running it.
*   **Dependencies are all mainstream, inspectable PyPI packages**: `PyQt6`, `winsdk` (Microsoft's own Windows SDK bindings), `syncedlyrics`, `qasync`, `keyboard`. Check any of them on PyPI/GitHub yourself.

If you're sharing this in a Discord server, the best answer to "is this safe?" is always "read it yourself" — link people to this section and to the source files above.

# 🤝 Contributions

KaraokeBird is an open-source project, and contributions are welcome!

1.  Fork the repository.
2.  Create your feature branch (`git checkout -b feature/AmazingFeature`).
3.  Run the tests (`pip install -r requirements-dev.txt`, then `pytest`). They cover the pure logic: LRC parsing, sync timing, geometry, settings validation, caches.
4.  Commit your changes (`git commit -m 'Add some AmazingFeature'`).
5.  Push to the branch (`git push origin feature/AmazingFeature`).
6.  Open a Pull Request.

# 📝 License

Distributed under the MIT License. See `LICENSE` for more information.
