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


def first_practice_event(server_dir):
    events_path = os.path.join(server_dir, "events_practice.json")
    if not os.path.isfile(events_path):
        return None
    try:
        events = load_json(events_path, {}).get("events", [])
        if not events:
            return None
        event = events[0]
        track = event.get("track", "")
        layout = event.get("layout", "")
        name = event.get("name", event.get("event_name", ""))
        length = event.get("track_length", event.get("length", 0))
        return {"track": track, "layout": layout, "event_name": name, "track_length": int(length)}
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


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
        handle.write('amplog() { echo "[$(date +%H:%M:%S)] [$1/$2]  : $3"; }\n')
        handle.write('amplog "Launch Info" "Info" "Starting AssettoCorsaEVOServer.exe via Proton"\n')
        http_label = "disabled" if not http_port else str(http_port)
        handle.write(f'amplog "Launch Info" "Info" "Ports TCP/UDP {tcp_port}, HTTP {http_label}"\n')
        handle.write(
            f'"${{0%/*}}/../.proton/proton" runinprefix '
            f'"${{0%/*}}/AssettoCorsaEVOServer.exe" '
            f'-serverconfig {serverconfig} '
            f'-seasondefinition {seasondefinition} 2>&1 &\n'
        )
        handle.write("SERVER_PID=$!\n")
        # ACE writes its game log under the Wine prefix; mirror it into AMP's console.
        handle.write(
            'ACELOG="${STEAM_COMPAT_DATA_PATH:-${0%/*}/../.proton/compatdata}'
            '/pfx/drive_c/users/steamuser/Saved Games/ACE-Server/'
            'Assetto Corsa EVO Server.txt"\n'
        )
        handle.write("LOG_PID=\"\"\n")
        handle.write(
            '( for _ in $(seq 1 60); do [ -f "$ACELOG" ] && break; sleep 1; done; '
            'tail -n 0 -F "$ACELOG" 2>/dev/null ) &\n'
        )
        handle.write("LOG_PID=$!\n")
        handle.write(
            'cleanup() { amplog "Launch Info" "Info" "Stopping server"; '
            "kill $SERVER_PID $LOG_PID 2>/dev/null; "
            "wait $SERVER_PID 2>/dev/null; exit; }\n"
        )
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

    settings = load_json(os.path.join(cfg_dir, "server.json"), {})
    season = load_json(os.path.join(cfg_dir, "season.json"), {})

    event = season.get("event", {})
    if not event.get("track"):
        discovered = first_practice_event(server_dir)
        if discovered:
            event = discovered
            season["event"] = event

    if not event.get("track"):
        print(
            "ERROR: No track configured in cfg/season.json and events_practice.json could not be read. "
            "Set Track ID / Layout / Event Name in Configuration, or ensure the server installed correctly.",
            file=sys.stderr,
        )
        sys.exit(1)

    season.setdefault("export_json", False)
    game_type = season.setdefault("game_type", "GameModeType_PRACTICE")
    season.setdefault("weather_type", "GameModeSelectionWeatherType_CLEAR")
    season.setdefault("weather_behaviour", "GameModeSelectionWeatherBehaviour_STATIC")
    season.setdefault("initial_grip", "InitialGrip_GREEN")

    if event.get("track_length") is not None:
        event["track_length"] = str(event["track_length"])

    game_config = season.get("game_config") or {}
    game_config.setdefault("practice_duration", 1200)
    game_config.setdefault("practice_overtime_waiting_next_session", 10)
    game_config.setdefault("practice_max_wait_to_box", 10)
    apply_practice_time(game_config)
    season["game_config"] = game_config

    # AC EVO expects TCP and UDP on the same port (AMP GamePort / Protocol Both).
    game_port = int(
        settings.get(
            "server_udp_listener_port",
            settings.get("server_tcp_listener_port", 9700),
        )
    )
    if game_port <= 0 or game_port > 65535:
        print(f"ERROR: Invalid game port {game_port}. Set Game Port in AMP (1-65535).", file=sys.stderr)
        sys.exit(1)
    tcp_port = game_port
    udp_port = game_port

    http_enabled = as_bool(settings.get("enable_http_api"), True)
    try:
        http_port = int(settings.get("server_http_port", 8081))
    except (TypeError, ValueError):
        http_port = 8081
    if not http_enabled or http_port <= 0:
        http_port = 0
        http_enabled = False
    elif http_port > 65535:
        print(f"ERROR: Invalid HTTP port {http_port}. Set HTTP/API Port in AMP (0-65535).", file=sys.stderr)
        sys.exit(1)

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
        "seasondefinition": encode_payload(season),
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
