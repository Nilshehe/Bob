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
    """Ladda nycklar från .env i projektmappen om de inte redan finns i miljön."""
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
    """Starta en enkel inloggning via Spotify OAuth och fånga callbacken lokalt."""
    manager = _build_auth_manager()
    auth_url = manager.get_authorize_url()
    webbrowser.open(auth_url)
    print("Öppnade Spotify-inloggning i webbläsaren.")
    print("Vänta tills du har loggat in och blivit omdirigerad tillbaka.")

    code = _wait_for_oauth_code()
    if not code:
        return "Inloggningen avbröts eller tog för lång tid. Försök igen."

    token_info = manager.get_access_token(code, as_dict=False, check_cache=False)
    if not token_info:
        return "Kunde inte hämta en giltig Spotify-token. Försök igen."

    global sp
    sp = spotipy.Spotify(auth_manager=manager)
    return "Inloggning lyckades. Du kan nu använda Spotify-kommandon."


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
            raise RuntimeError("Spotify är inte inloggad. Kör spotify_login() först.")

    sp = spotipy.Spotify(auth_manager=manager)
    return sp
 
 
# ---------- Actions ----------
#@tool
def spotify_control(action: str, query: Optional[str] = None) -> str:
    """Styr Spotify: play, pause, next, previous, search_and_play, current, volume:<0-100>"""
    try:
        _get_spotify_client()
        if action == "play":
            sp.start_playback()
            return "Fortsätter uppspelning."
 
        elif action == "pause":
            sp.pause_playback()
            return "Pausad."
 
        elif action == "next":
            sp.next_track()
            return "Nästa låt."
 
        elif action == "previous":
            sp.previous_track()
            return "Föregående låt."
 
        elif action == "current":
            track = sp.current_playback()
            if not track or not track.get("item"):
                return "Inget spelas just nu."
            item = track["item"]
            artists = ", ".join(a["name"] for a in item["artists"])
            return f"Spelar just nu: {item['name']} - {artists}"
 
        elif action == "search_and_play":
            if not query:
                return "Ange en sökfråga (låt/artist) för search_and_play."
            results = sp.search(q=query, limit=1, type="track")
            tracks = results["tracks"]["items"]
            if not tracks:
                return f"Hittade ingen låt för '{query}'."
            uri = tracks[0]["uri"]
            sp.start_playback(uris=[uri])
            artists = ", ".join(a["name"] for a in tracks[0]["artists"])
            return f"Spelar nu: {tracks[0]['name']} - {artists}"
 
        elif action.startswith("volume:"):
            try:
                vol = int(action.split(":")[1])
                vol = max(0, min(100, vol))
            except (IndexError, ValueError):
                return "Ogiltig volym. Använd t.ex. 'volume:50'."
            sp.volume(vol)
            return f"Volym satt till {vol}%."
 
        elif action.startswith("shuffle:"):
            state = action.split(":")[1].strip().lower()
            if state not in ("on", "off"):
                return "Ogiltigt shuffle-läge. Använd 'shuffle:on' eller 'shuffle:off'."
            sp.shuffle(state == "on")
            return f"Shuffle {'på' if state == 'on' else 'av'}."
 
        elif action.startswith("repeat:"):
            mode = action.split(":")[1].strip().lower()
            if mode not in ("track", "context", "off"):
                return "Ogiltigt repeat-läge. Använd 'repeat:track', 'repeat:context' eller 'repeat:off'."
            sp.repeat(mode)
            labels = {"track": "låt", "context": "spellista/album", "off": "av"}
            return f"Repeat satt till: {labels[mode]}."
 
        elif action == "add_to_queue":
            if not query:
                return "Ange en sökfråga (låt/artist) för add_to_queue."
            results = sp.search(q=query, limit=1, type="track")
            tracks = results["tracks"]["items"]
            if not tracks:
                return f"Hittade ingen låt för '{query}'."
            sp.add_to_queue(tracks[0]["uri"])
            artists = ", ".join(a["name"] for a in tracks[0]["artists"])
            return f"Lade till i kön: {tracks[0]['name']} - {artists}"
 
        elif action == "list_devices":
            devices = sp.devices().get("devices", [])
            if not devices:
                return "Inga aktiva enheter hittades. Öppna Spotify på en enhet."
            lines = [
                f"{d['name']} ({d['type']}){' [aktiv]' if d['is_active'] else ''} - id: {d['id']}"
                for d in devices
            ]
            return "Tillgängliga enheter:\n" + "\n".join(lines)
 
        elif action == "transfer_device":
            if not query:
                return "Ange enhetsnamn (query) för transfer_device."
            devices = sp.devices().get("devices", [])
            match = next(
                (d for d in devices if query.lower() in d["name"].lower()), None
            )
            if not match:
                return f"Hittade ingen enhet matchande '{query}'. Använd 'list_devices' för att se namn."
            sp.transfer_playback(device_id=match["id"], force_play=True)
            return f"Bytte uppspelning till: {match['name']}"
 
        else:
            return (
                f"Okänd action '{action}'. Giltiga: play, pause, next, previous, "
                "current, search_and_play, volume:<0-100>, shuffle:<on|off>, "
                "repeat:<track|context|off>, add_to_queue, list_devices, transfer_device."
            )
 
    except spotipy.SpotifyException as e:
        return f"Spotify-fel: {e}"
    except Exception as e:
        return f"Fel: {e}"
 
 
class SpotifyInput(BaseModel):
    action: str = Field(
        description=(
            "En av: 'play', 'pause', 'next', 'previous', 'current', "
            "'search_and_play', 'volume:<0-100>', 'shuffle:<on|off>', "
            "'repeat:<track|context|off>', 'add_to_queue', 'list_devices', "
            "'transfer_device'"
        )
    )
    query: Optional[str] = Field(
        default=None,
        description=(
            "Sökfråga (låt/artist) - krävs för 'search_and_play' och "
            "'add_to_queue'. Enhetsnamn - krävs för 'transfer_device'."
        ),
    )
 
 
 
 
# ---------- Exempel på användning ----------
if __name__ == "__main__":
    # Testa direkt utan agent
    print(spotify_control("current"))