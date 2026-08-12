import os
from pathlib import Path
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import CacheFileHandler
from langchain.tools import tool
from pydantic import BaseModel, Field
from typing import Optional


def load_env_file() -> None:
    """Load keys from a .env file in the project directory if they are not already present in the environment."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file()

SCOPE = (
    "user-modify-playback-state user-read-playback-state "
    "user-read-currently-playing"
)
 
# Spara token-cachen utanför projektmappen (t.ex. ~/.cache/spotify_tool/token.json)
# så den inte hamnar i VS Code-workspacet eller riskerar att committas till git.
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "spotify_tool")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_PATH = os.path.join(CACHE_DIR, "token.json")
 
cache_handler = CacheFileHandler(cache_path=CACHE_PATH)
auth_manager = None
sp = None


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        self.server.oauth_code = parse_qs(parsed.query).get("code", [None])[0]  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<html><body><h3>Du kan nu stanga detta fonster.</h3></body></html>")
        self.wfile.write(b"<html><body><h3>You may now close this window.</h3></body></html>")

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return None


def _build_auth_manager() -> SpotifyOAuth:
    global auth_manager
    if auth_manager is None:
        auth_manager = SpotifyOAuth(
            scope=SCOPE,
            cache_handler=cache_handler,
            client_id=os.getenv("SPOTIPY_CLIENT_ID"),
            client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
            redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
            open_browser=False,
            show_dialog=True,
        )
    return auth_manager


def _wait_for_oauth_code(port: int = 8080, timeout_seconds: int = 90) -> Optional[str]:
    httpd = HTTPServer(("127.0.0.1", port), OAuthCallbackHandler)
    httpd.timeout = timeout_seconds
    httpd.oauth_code = None  # type: ignore[attr-defined]
    try:
        httpd.handle_request()
    except OSError:
        return None
    return httpd.oauth_code  # type: ignore[return-value]


def spotify_login() -> str:
    """Start a simple Spotify OAuth login flow and capture the callback locally."""
    manager = _build_auth_manager()
    auth_url = manager.get_authorize_url()
    webbrowser.open(auth_url)
    print("Opened Spotify login in browser.")
    print("Wait until you have logged in and been redirected back.")

    code = _wait_for_oauth_code()
    if not code:
        return "Login cancelled or timed out. Please try again."

    token_info = manager.get_access_token(code, as_dict=False, check_cache=False)
    if not token_info:
        return "Could not obtain a valid Spotify token. Please try again."

    global sp
    sp = spotipy.Spotify(auth_manager=manager)
    return "Login succeeded. You can now use Spotify commands."


def _get_spotify_client() -> spotipy.Spotify:
    global sp
    if sp is not None:
        return sp

    manager = _build_auth_manager()
    cached_token = manager.get_cached_token()
    if not cached_token:
        spotify_login()
        cached_token = manager.get_cached_token()
        if not cached_token:
            raise RuntimeError("Spotify is not logged in. Run spotify_login() first.")

    sp = spotipy.Spotify(auth_manager=manager)
    return sp
 
 
# ---------- Actions ----------
#@tool
def spotify_control(action: str, query: Optional[str] = None) -> str:
    """Control Spotify: play, pause, next, previous, search_and_play, current, volume:<0-100>"""
    try:
        _get_spotify_client()
        if action == "play":
            sp.start_playback()
            return "Resuming playback."
 
        elif action == "pause":
            sp.pause_playback()
            return "Paused."
 
        elif action == "next":
            sp.next_track()
            return "Next track."
 
        elif action == "previous":
            sp.previous_track()
            return "Previous track."
 
        elif action == "current":
            track = sp.current_playback()
            if not track or not track.get("item"):
                return "Nothing is playing right now."
            item = track["item"]
            artists = ", ".join(a["name"] for a in item["artists"])
            return f"Now playing: {item['name']} - {artists}"
 
        elif action == "search_and_play":
            if not query:
                return "Provide a search query (song/artist) for search_and_play."
            results = sp.search(q=query, limit=1, type="track")
            tracks = results["tracks"]["items"]
            if not tracks:
                return f"No track found for '{query}'."
            uri = tracks[0]["uri"]
            sp.start_playback(uris=[uri])
            artists = ", ".join(a["name"] for a in tracks[0]["artists"])
            return f"Now playing: {tracks[0]['name']} - {artists}"
 
        elif action.startswith("volume:"):
            try:
                vol = int(action.split(":")[1])
                vol = max(0, min(100, vol))
            except (IndexError, ValueError):
                return "Invalid volume. Use e.g. 'volume:50'."
            sp.volume(vol)
            return f"Volume set to {vol}%."
 
        elif action.startswith("shuffle:"):
            state = action.split(":")[1].strip().lower()
            if state not in ("on", "off"):
                return "Invalid shuffle state. Use 'shuffle:on' or 'shuffle:off'."
            sp.shuffle(state == "on")
            return f"Shuffle {'on' if state == 'on' else 'off'}."
 
        elif action.startswith("repeat:"):
            mode = action.split(":")[1].strip().lower()
            if mode not in ("track", "context", "off"):
                return "Invalid repeat mode. Use 'repeat:track', 'repeat:context' or 'repeat:off'."
            sp.repeat(mode)
            labels = {"track": "track", "context": "playlist/album", "off": "off"}
            return f"Repeat set to: {labels[mode]}."
 
        elif action == "add_to_queue":
            if not query:
                return "Provide a search query (song/artist) for add_to_queue."
            results = sp.search(q=query, limit=1, type="track")
            tracks = results["tracks"]["items"]
            if not tracks:
                return f"No track found for '{query}'."
            sp.add_to_queue(tracks[0]["uri"])
            artists = ", ".join(a["name"] for a in tracks[0]["artists"])
            return f"Added to queue: {tracks[0]['name']} - {artists}"
 
        elif action == "list_devices":
            devices = sp.devices().get("devices", [])
            if not devices:
                return "No active devices found. Open Spotify on a device."
            lines = [
                f"{d['name']} ({d['type']}){' [active]' if d['is_active'] else ''} - id: {d['id']}"
                for d in devices
            ]
            return "Available devices:\n" + "\n".join(lines)
 
        elif action == "transfer_device":
            if not query:
                return "Ange enhetsnamn (query) för transfer_device."
            devices = sp.devices().get("devices", [])
            match = next(
                (d for d in devices if query.lower() in d["name"].lower()), None
            )
            if not match:
                return f"No device matching '{query}' found. Use 'list_devices' to view names."
            sp.transfer_playback(device_id=match["id"], force_play=True)
            return f"Switched playback to: {match['name']}"
 
        else:
            return (
                f"Unknown action '{action}'. Valid: play, pause, next, previous, "
                "current, search_and_play, volume:<0-100>, shuffle:<on|off>, "
                "repeat:<track|context|off>, add_to_queue, list_devices, transfer_device."
            )
 
    except spotipy.SpotifyException as e:
        return f"Spotify error: {e}"
    except Exception as e:
        return f"Error: {e}"
 
 
class SpotifyInput(BaseModel):
    action: str = Field(
        description=(
            "One of: 'play', 'pause', 'next', 'previous', 'current', "
            "'search_and_play', 'volume:<0-100>', 'shuffle:<on|off>', "
            "'repeat:<track|context|off>', 'add_to_queue', 'list_devices', "
            "'transfer_device'"
        )
    )
    query: Optional[str] = Field(
        default=None,
        description=(
            "Search query (song/artist) - required for 'search_and_play' and "
            "'add_to_queue'. Device name - required for 'transfer_device'."
        ),
    )
 
 
 
 
# ---------- Exempel på användning ----------
if __name__ == "__main__":
    # Testa direkt utan agent
    print(spotify_control("current"))