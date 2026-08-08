#!/usr/bin/env python3
"""Build compressed -serverconfig / -seasondefinition payloads for AssettoCorsaEVOServer.exe."""
import base64
import json
import os
import socket
import struct
import sys
import zlib
from datetime import datetime

BASE = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

LAUNCH_PATHS = {
    "GameModeType_PRACTICE": "content\\data\\practice.seasondefinition",
    "GameModeType_RACE_WEEKEND": "content\\data\\race_weekend.seasondefinition",
}
VALID_TUNING_TYPES = {"TuningAllowed", "TuningDenied"}


def amplog(component, level, message):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{component}/{level}]  : {message}")


def as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    if isinstance(value, (int, float)):
        return value != 0
    return default


def clean_str(value, default=""):
    if value is None:
        return default
    return str(value).strip()


def encode_payload(obj):
    data = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    compressed = zlib.compress(data)
    return base64.b64encode(struct.pack(">I", len(data)) + compressed).decode("ascii")


def load_json(path, default=None):
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (json.JSONDecodeError, ValueError):
            print(f"WARNING: Failed to parse {path}, using defaults.", file=sys.stderr)
    return default if default is not None else {}


def event_catalog(server_dir):
    """Load practice (+ race weekend) events for track/layout validation."""
    events = []
    for name in ("events_practice.json", "events_race_weekend.json"):
        path = os.path.join(server_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            events.extend(load_json(path, {}).get("events", []) or [])
        except (TypeError, ValueError):
            continue
    return events


def normalize_catalog_event(raw):
    if not isinstance(raw, dict):
        return None
    track = clean_str(raw.get("track", ""))
    layout = clean_str(raw.get("layout", ""))
    if not track:
        return None
    name = clean_str(raw.get("name") or raw.get("event_name") or "")
    length = raw.get("track_length", raw.get("length", 0))
    try:
        length = int(length or 0)
    except (TypeError, ValueError):
        length = 0
    return {
        "track": track,
        "layout": layout,
        "event_name": name,
        "track_length": length,
    }


def first_practice_event(server_dir):
    for raw in event_catalog(server_dir):
        normalized = normalize_catalog_event(raw)
        if normalized:
            return normalized
    return None


def split_combined_layout(value):
    """Parse mistaken UI values like 'Nurburgring - Touristenfahrten'."""
    text = clean_str(value)
    if " - " not in text:
        return None, None
    left, right = text.split(" - ", 1)
    left, right = clean_str(left), clean_str(right)
    if left and right:
        return left, right
    return None, None


def find_catalog_event(catalog, track="", layout="", event_name=""):
    track = clean_str(track)
    layout = clean_str(layout)
    event_name = clean_str(event_name)
    normalized = [e for e in (normalize_catalog_event(raw) for raw in catalog) if e]
    if not normalized:
        return None

    def match(pred):
        for item in normalized:
            if pred(item):
                return item
        return None

    if track and layout and event_name:
        hit = match(
            lambda e: e["track"] == track
            and e["layout"] == layout
            and e["event_name"] == event_name
        )
        if hit:
            return hit
    if track and layout:
        hit = match(lambda e: e["track"] == track and e["layout"] == layout)
        if hit:
            return hit
    if track and event_name:
        hit = match(lambda e: e["track"] == track and e["event_name"] == event_name)
        if hit:
            return hit
    if track and not layout:
        hit = match(lambda e: e["track"] == track)
        if hit:
            return hit
    if layout and not track:
        layout_hits = [e for e in normalized if e["layout"] == layout]
        if len(layout_hits) == 1:
            return layout_hits[0]
    return None


def resolve_season_event(server_dir, event):
    """
    Resolve AMP season event fields to a catalog entry.
    Rejects empty-track custom layouts that previously fell through to Brands Hatch.
    """
    event = dict(event or {})
    track = clean_str(event.get("track", ""))
    layout = clean_str(event.get("layout", ""))
    event_name = clean_str(event.get("event_name", ""))
    try:
        track_length = int(event.get("track_length") or 0)
    except (TypeError, ValueError):
        track_length = 0

    # Fix combined strings pasted into Layout (or Track).
    if not track and layout:
        maybe_track, maybe_layout = split_combined_layout(layout)
        if maybe_track:
            amplog(
                "Track Fix",
                "Warning",
                f"Layout '{layout}' looks combined; treating as track='{maybe_track}' layout='{maybe_layout}'",
            )
            track, layout = maybe_track, maybe_layout
    if track and " - " in track and not layout:
        maybe_track, maybe_layout = split_combined_layout(track)
        if maybe_track:
            track, layout = maybe_track, maybe_layout

    catalog = event_catalog(server_dir)
    wants_custom = bool(track or layout or event_name or track_length)

    if not wants_custom:
        discovered = first_practice_event(server_dir)
        if discovered:
            amplog(
                "Track Info",
                "Info",
                f"No track configured; using first catalog event "
                f"{discovered['track']} / {discovered['layout']}",
            )
            return discovered
        return None

    hit = find_catalog_event(catalog, track=track, layout=layout, event_name=event_name)
    if hit:
        # Prefer catalog length/name when AMP left them empty/zero.
        if not event_name:
            event_name = hit["event_name"]
        if track_length <= 0:
            track_length = hit["track_length"]
        if not track:
            track = hit["track"]
        if not layout:
            layout = hit["layout"]
        # If user supplied a name/length that differs, keep catalog match identity
        # but allow explicit non-empty overrides for name/length when provided.
        resolved = {
            "track": hit["track"],
            "layout": hit["layout"],
            "event_name": event_name or hit["event_name"],
            "track_length": track_length if track_length > 0 else hit["track_length"],
        }
        amplog(
            "Track Info",
            "Info",
            f"Using {resolved['track']} / {resolved['layout']} "
            f"({resolved['event_name']}, {resolved['track_length']}m)",
        )
        return resolved

    # Unknown custom values: fail clearly instead of silently using Brands Hatch.
    samples = []
    for raw in catalog[:8]:
        item = normalize_catalog_event(raw)
        if item:
            samples.append(f"{item['track']} / {item['layout']}")
    sample_txt = "; ".join(samples) if samples else "(no events_*.json found)"
    print(
        "ERROR: Track/layout not found in server catalog.\n"
        f"  Got track={track!r} layout={layout!r} event_name={event_name!r}\n"
        "  Use exact ids from events_practice.json, for example:\n"
        "    Track ID: Nurburgring\n"
        "    Layout: Touristenfahrten\n"
        "    Event Name: Touristenfahrten Time Attack\n"
        f"  Examples: {sample_txt}",
        file=sys.stderr,
    )
    return None


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def resolve_http_port(settings, settings_path):
    """
    Apply Enable HTTP/API + port. When disabled, force port 0 into server.json so
    AMP $HTTPPort / firewall reservation stays aligned after the next settings sync.
    """
    http_enabled = as_bool(settings.get("enable_http_api"), True)
    try:
        http_port = int(settings.get("server_http_port", 8081))
    except (TypeError, ValueError):
        http_port = 8081

    preferred = settings.get("server_http_port_preferred")
    try:
        preferred = int(preferred) if preferred not in (None, "") else None
    except (TypeError, ValueError):
        preferred = None

    if not http_enabled:
        if http_port > 0:
            settings["server_http_port_preferred"] = http_port
        elif preferred and preferred > 0:
            settings["server_http_port_preferred"] = preferred
        http_port = 0
        amplog(
            "Port Info",
            "Info",
            "HTTP/API disabled (port 0). AMP firewall for HTTP closes when HTTP/API Port is 0. "
            "Save in Configuration if the Ports UI still shows the old value.",
        )
    else:
        if http_port <= 0:
            http_port = preferred if preferred and preferred > 0 else 8081
            amplog(
                "Port Info",
                "Info",
                f"HTTP/API enabled; restored port {http_port} (was 0).",
            )
        if http_port > 65535:
            print(
                f"ERROR: Invalid HTTP port {http_port}. Set HTTP/API Port in AMP (0-65535).",
                file=sys.stderr,
            )
            sys.exit(1)
        settings["server_http_port_preferred"] = http_port

    settings["enable_http_api"] = http_enabled
    settings["server_http_port"] = http_port
    try:
        save_json(settings_path, settings)
    except OSError as exc:
        amplog("Port Warning", "Warning", f"Could not write {settings_path}: {exc}")

    return http_enabled, http_port


def build_allowed_cars(server_dir):
    cars_path = os.path.join(server_dir, "cars.json")
    if not os.path.isfile(cars_path):
        return []
    try:
        cars = load_json(cars_path, {}).get("cars", [])
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    selected = [car for car in cars if car.get("is_selected")]
    if not selected:
        selected = cars
    return [
        {
            "car_name": car.get("name", ""),
            "ballast": int(round(car.get("ballast", 0))),
            "restrictor": float(car.get("restrictor", 0)),
        }
        for car in selected
        if car.get("name")
    ]


def launch_path_for(game_type):
    return LAUNCH_PATHS.get(game_type, LAUNCH_PATHS["GameModeType_PRACTICE"])


def tuning_type_for(settings):
    tuning_type = clean_str(settings.get("tuning_type", "TuningAllowed"), "TuningAllowed")
    if tuning_type not in VALID_TUNING_TYPES:
        print(f"WARNING: Unknown tuning_type '{tuning_type}', using TuningAllowed.", file=sys.stderr)
        return "TuningAllowed"
    return tuning_type


def server_installed(server_dir):
    return os.path.isfile(os.path.join(server_dir, "AssettoCorsaEVOServer.exe"))


def process_uid():
    getuid = getattr(os, "getuid", None)
    if callable(getuid):
        return str(getuid())
    return "n/a"


def tcp_in_use(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False


def apply_practice_time(game_config):
    """Apply AMP hour_of_day / time_multiplier into practice_time_of_day every start."""
    hour = game_config.pop("hour_of_day", None)
    multiplier = game_config.pop("time_multiplier", None)
    tod = game_config.get("practice_time_of_day")
    if not isinstance(tod, dict):
        tod = {
            "year": 2024,
            "month": 8,
            "day": 15,
            "hour": 16,
            "minute": 0,
            "second": 0,
            "time_multiplier": 1,
        }
        game_config["practice_time_of_day"] = tod
    if hour is not None:
        tod["hour"] = max(0, min(23, int(hour)))
    if multiplier is not None:
        tod["time_multiplier"] = max(1, int(multiplier))
    tod.setdefault("year", 2024)
    tod.setdefault("month", 8)
    tod.setdefault("day", 15)
    tod.setdefault("minute", 0)
    tod.setdefault("second", 0)


def write_linux_wrapper(server_dir, serverconfig, seasondefinition, tcp_port, http_port):
    wrapper = os.path.join(server_dir, "launch_server.sh")
    with open(wrapper, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("#!/bin/bash\n")
        # Own process group so AMP OS_CLOSE / TERM can tear down Proton+Wine children.
        handle.write("set -m\n")
        handle.write('amplog() { echo "[$(date +%H:%M:%S)] [$1/$2]  : $3"; }\n')
        handle.write('amplog "Launch Info" "Info" "Starting AssettoCorsaEVOServer.exe via Proton"\n')
        http_label = "disabled" if not http_port else str(http_port)
        handle.write(f'amplog "Launch Info" "Info" "Ports TCP/UDP {tcp_port}, HTTP {http_label}"\n')
        handle.write('ROOT="$(cd "${0%/*}/.." && pwd)"\n')
        handle.write('SERVER_DIR="${0%/*}"\n')
        handle.write(
            '"$ROOT/.proton/proton" runinprefix '
            '"$SERVER_DIR/AssettoCorsaEVOServer.exe" '
            f'-serverconfig {serverconfig} '
            f'-seasondefinition {seasondefinition} 2>&1 &\n'
        )
        handle.write("SERVER_PID=$!\n")
        # ACE writes its game log under the Wine prefix; mirror it into AMP's console.
        handle.write(
            'ACELOG="${STEAM_COMPAT_DATA_PATH:-$ROOT/.proton/compatdata}'
            '/pfx/drive_c/users/steamuser/Saved Games/ACE-Server/'
            'Assetto Corsa EVO Server.txt"\n'
        )
        handle.write("LOG_PID=\"\"\n")
        handle.write(
            '( for _ in $(seq 1 60); do [ -f "$ACELOG" ] && break; sleep 1; done; '
            'tail -n 0 -F "$ACELOG" 2>/dev/null ) &\n'
        )
        handle.write("LOG_PID=$!\n")
        handle.write("cleanup() {\n")
        handle.write('  amplog "Launch Info" "Info" "Stopping server (full Proton/Wine tree)"\n')
        handle.write("  trap - INT TERM EXIT\n")
        handle.write("  kill $LOG_PID 2>/dev/null || true\n")
        handle.write("  # Kill process group first (Proton python + children).\n")
        handle.write("  kill -TERM -$SERVER_PID $SERVER_PID 2>/dev/null || true\n")
        handle.write("  sleep 1\n")
        handle.write("  # Wine often reparents AssettoCorsaEVOServer/wineserver; sweep by path.\n")
        handle.write(
            '  pkill -TERM -f "$SERVER_DIR/AssettoCorsaEVOServer.exe" 2>/dev/null || true\n'
        )
        handle.write(
            '  pkill -TERM -f "$ROOT/.proton/files/.*/wineserver" 2>/dev/null || true\n'
        )
        handle.write(
            '  pkill -TERM -f "$ROOT/.proton/compatdata" 2>/dev/null || true\n'
        )
        handle.write("  sleep 1\n")
        handle.write(
            '  pkill -KILL -f "$SERVER_DIR/AssettoCorsaEVOServer.exe" 2>/dev/null || true\n'
        )
        handle.write("  kill -KILL -$SERVER_PID $SERVER_PID 2>/dev/null || true\n")
        handle.write("  wait $SERVER_PID 2>/dev/null || true\n")
        handle.write("}\n")
        handle.write("trap cleanup INT TERM\n")
        handle.write(f"GP={tcp_port}\n")
        handle.write(f"HP={http_port}\n")
        handle.write("check_ports() {\n")
        handle.write("  local LABEL=$1\n")
        handle.write("  if ! kill -0 $SERVER_PID 2>/dev/null; then\n")
        handle.write("    wait $SERVER_PID 2>/dev/null\n")
        handle.write('    amplog "Monitor Error" "Error" "Server exited before $LABEL (code $?)"\n')
        handle.write("    return 1\n")
        handle.write("  fi\n")
        handle.write('  amplog "Monitor Info" "Info" "$LABEL"\n')
        handle.write('  TCP_L=$(ss -tlnp 2>/dev/null | grep -F ":${GP} " || true)\n')
        handle.write('  UDP_L=$(ss -ulnp 2>/dev/null | grep -F ":${GP} " || true)\n')
        handle.write(
            '[ -n "$TCP_L" ] && amplog "Monitor Info" "Info" "TCP ${GP}: listening" '
            '|| amplog "Monitor Warning" "Warning" "TCP ${GP}: not listening"\n'
        )
        handle.write(
            '[ -n "$UDP_L" ] && amplog "Monitor Info" "Info" "UDP ${GP}: listening" '
            '|| amplog "Monitor Warning" "Warning" "UDP ${GP}: not listening"\n'
        )
        handle.write("  if [ \"$HP\" -gt 0 ] 2>/dev/null; then\n")
        handle.write('    HTTP_L=$(ss -tlnp 2>/dev/null | grep -F ":${HP} " || true)\n')
        handle.write(
            '    [ -n "$HTTP_L" ] && amplog "Monitor Info" "Info" "HTTP ${HP}: listening" '
            '|| amplog "Monitor Warning" "Warning" "HTTP ${HP}: not listening"\n'
        )
        handle.write("  else\n")
        handle.write('    amplog "Monitor Info" "Info" "HTTP: disabled"\n')
        handle.write("  fi\n")
        # AMP AppReadyRegex: emit a console line AMP can see (ACE logs often go to a Wine file).
        handle.write(
            'if [ -n "$TCP_L" ] && [ -n "$UDP_L" ]; then '
            'echo "Listening to TCP ${GP} | UDP ${GP}"; fi\n'
        )
        handle.write("}\n")
        handle.write('sleep 5 && check_ports "5s post-launch" || true\n')
        handle.write('sleep 15 && check_ports "20s post-launch" || true\n')
        handle.write('sleep 40 && check_ports "60s post-launch" || true\n')
        handle.write("wait $SERVER_PID 2>/dev/null\n")
        handle.write("EC=$?\n")
        handle.write("kill $LOG_PID 2>/dev/null\n")
        handle.write('amplog "Launch Info" "Info" "Server exited with code: $EC"\n')
        handle.write("exit $EC\n")
    try:
        os.chmod(wrapper, 0o755)
    except OSError:
        pass
    return wrapper


def write_windows_wrapper(server_dir, serverconfig, seasondefinition, tcp_port, http_port):
    wrapper = os.path.join(server_dir, "launch_server.bat")
    with open(wrapper, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write("@echo off\r\n")
        handle.write("setlocal\r\n")
        http_label = "disabled" if not http_port else str(http_port)
        handle.write(f"echo [Launch Info] Ports TCP/UDP {tcp_port}, HTTP {http_label}\r\n")
        handle.write(
            f'AssettoCorsaEVOServer.exe -serverconfig {serverconfig} '
            f"-seasondefinition {seasondefinition}\r\n"
        )
        handle.write("exit /b %ERRORLEVEL%\r\n")
    return wrapper


def main():
    cfg_dir = os.path.join(BASE, "cfg")
    server_dir = BASE
    os.makedirs(cfg_dir, exist_ok=True)

    if not server_installed(server_dir):
        print(
            "ERROR: Dedicated server files are not installed. Run Update on this instance and "
            "log in with a Steam account that owns Assetto Corsa EVO (App ID 3058630). "
            "Anonymous SteamCMD login returns 'No subscription' for app 4564210.",
            file=sys.stderr,
        )
        sys.exit(1)

    settings_path = os.path.join(cfg_dir, "server.json")
    season_path = os.path.join(cfg_dir, "season.json")
    settings = load_json(settings_path, {})
    season = load_json(season_path, {})

    event = resolve_season_event(server_dir, season.get("event", {}))
    if not event or not event.get("track"):
        print(
            "ERROR: No valid track configured in cfg/season.json and events catalog could not resolve one. "
            "Set Track ID / Layout / Event Name in Configuration to exact catalog values, "
            "or ensure the server installed correctly.",
            file=sys.stderr,
        )
        sys.exit(1)

    season["event"] = {
        "track": event["track"],
        "layout": event["layout"],
        "event_name": event["event_name"],
        "track_length": int(event["track_length"]),
    }

    season.setdefault("export_json", False)
    game_type = season.setdefault("game_type", "GameModeType_PRACTICE")
    season.setdefault("weather_type", "GameModeSelectionWeatherType_CLEAR")
    season.setdefault("weather_behaviour", "GameModeSelectionWeatherBehaviour_STATIC")
    season.setdefault("initial_grip", "InitialGrip_GREEN")

    game_config = season.get("game_config") or {}
    game_config.setdefault("practice_duration", 1200)
    game_config.setdefault("practice_overtime_waiting_next_session", 10)
    game_config.setdefault("practice_max_wait_to_box", 10)
    apply_practice_time(game_config)
    season["game_config"] = game_config

    try:
        # Persist resolved track so AMP UI / next start do not keep bad combined layouts.
        save_json(season_path, season)
    except OSError as exc:
        amplog("Track Warning", "Warning", f"Could not write {season_path}: {exc}")

    # ACE payload historically stringifies track_length (keep season.json numeric for AMP).
    payload_season = json.loads(json.dumps(season))
    payload_season["event"]["track_length"] = str(payload_season["event"]["track_length"])

    # AC EVO expects TCP and UDP on the same port. Never fail Start if AMP wrote 0.
    def _port(value, default=0):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return default
        return value if 0 < value <= 65535 else default

    udp_port = _port(settings.get("server_udp_listener_port"), 0)
    tcp_port = _port(settings.get("server_tcp_listener_port"), 0)
    game_port = udp_port or tcp_port or 9700
    tcp_port = game_port
    udp_port = game_port
    settings["server_tcp_listener_port"] = tcp_port
    settings["server_udp_listener_port"] = udp_port

    http_enabled, http_port = resolve_http_port(settings, settings_path)

    amplog("Prepare Info", "Info", f"Python {sys.version.split()[0]} | PID {os.getpid()} | UID {process_uid()}")
    amplog(
        "Prepare Info",
        "Info",
        f"Ports TCP/UDP={tcp_port} HTTP={'off' if not http_enabled else http_port}",
    )

    if http_enabled and tcp_in_use(http_port):
        amplog(
            "Port Check",
            "Warning",
            f"HTTP port {http_port} appears in use. Change HTTP/API Port in AMP instead of auto-bumping "
            "(auto-bump would desync AMP firewall rules).",
        )
    if tcp_in_use(tcp_port):
        amplog("Port Check", "Warning", f"Game TCP port {tcp_port} appears in use before launch.")

    config = {
        "server_tcp_listener_port": tcp_port,
        "server_udp_listener_port": udp_port,
        "server_tcp_internal_port": tcp_port,
        "server_udp_internal_port": udp_port,
        "server_http_port": http_port,
        "server_name": clean_str(
            settings.get("server_name", "Assetto Corsa EVO Server - Powered by AMP"),
            "Assetto Corsa EVO Server - Powered by AMP",
        ),
        "launch_path": launch_path_for(game_type),
        "netcode_update_interval": int(settings.get("netcode_update_interval", 20)),
        "driver_password": clean_str(settings.get("driver_password", "")),
        "spectator_password": clean_str(settings.get("spectator_password", "")),
        "max_players": int(settings.get("max_players", 16)),
        "allowed_cars_list_full": build_allowed_cars(server_dir),
        "type": clean_str(settings.get("type", "MultiplayerServerListSessionType_RANKED")),
        "cycle": as_bool(settings.get("cycle"), True),
        "admin_password": clean_str(settings.get("admin_password", "")),
        "pi_min": float(settings.get("pi_min", 0.0)),
        "pi_max": float(settings.get("pi_max", 100.0)),
        "property_1": as_bool(settings.get("property_1"), False),
        "property_2": as_bool(settings.get("property_2"), False),
        "property_3": as_bool(settings.get("property_3"), False),
        "entry_list_server_url": clean_str(settings.get("entry_list_server_url", "")),
        "results_post_url": clean_str(settings.get("results_post_url", "")),
        "token": clean_str(settings.get("token", "")),
        "tuning_type": tuning_type_for(settings),
        "entry_list_path": clean_str(settings.get("entry_list_path", "")),
        "results_path": clean_str(settings.get("results_path", "")),
    }

    launch = {
        "serverconfig": encode_payload(config),
        "seasondefinition": encode_payload(payload_season),
    }

    with open(os.path.join(cfg_dir, "launch.json"), "w", encoding="utf-8") as handle:
        json.dump(launch, handle, indent=2)
        handle.write("\n")

    write_linux_wrapper(
        server_dir, launch["serverconfig"], launch["seasondefinition"], tcp_port, http_port
    )
    write_windows_wrapper(
        server_dir, launch["serverconfig"], launch["seasondefinition"], tcp_port, http_port
    )

    amplog(
        "Prepare Info",
        "Info",
        f"Prepared '{config['server_name']}' — wrote launch_server.sh and launch_server.bat",
    )


if __name__ == "__main__":
    main()
