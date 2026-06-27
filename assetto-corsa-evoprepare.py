#!/usr/bin/env python3
"""Build compressed -serverconfig / -seasondefinition payloads for AssettoCorsaEVOServer.exe."""
import base64
import json
import os
import struct
import sys
import zlib

BASE = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

LAUNCH_PATHS = {
    "GameModeType_PRACTICE": "content\\data\\practice.seasondefinition",
    "GameModeType_RACE_WEEKEND": "content\\data\\race_weekend.seasondefinition",
}
VALID_TUNING_TYPES = {"TuningAllowed", "TuningDenied"}


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
    game_config.setdefault("practice_time_of_day", {
        "year": 2024, "month": 8, "day": 15,
        "hour": int(game_config.get("hour_of_day", 16)),
        "minute": 0, "second": 0,
        "time_multiplier": int(game_config.get("time_multiplier", 1)),
    })
    game_config.setdefault("practice_overtime_waiting_next_session",
                           int(game_config.get("practice_overtime_waiting_next_session", 10)))
    game_config.setdefault("practice_max_wait_to_box",
                           int(game_config.get("practice_max_wait_to_box", 10)))
    game_config.pop("hour_of_day", None)
    game_config.pop("time_multiplier", None)
    season["game_config"] = game_config

    udp_port = int(settings.get("server_udp_listener_port", 9700))
    tcp_port = udp_port
    http_port = int(settings.get("server_http_port", 8081))

    import socket
    log_lines = []
    log_lines.append(f"[prepare] UDP port: {udp_port}, TCP port: {tcp_port}, HTTP port: {http_port}")
    for check_port in [tcp_port, udp_port, http_port]:
        for proto, stype in [("TCP", socket.SOCK_STREAM), ("UDP", socket.SOCK_DGRAM)]:
            try:
                with socket.socket(socket.AF_INET, stype) as s:
                    s.settimeout(0.5)
                    if stype == socket.SOCK_STREAM:
                        result = s.connect_ex(("127.0.0.1", check_port))
                        status = "IN USE" if result == 0 else "free"
                    else:
                        try:
                            s.bind(("0.0.0.0", check_port))
                            status = "free"
                        except OSError as e:
                            status = f"IN USE ({e})"
                    log_lines.append(f"[prepare]   {proto} {check_port}: {status}")
            except OSError as e:
                log_lines.append(f"[prepare]   {proto} {check_port}: error ({e})")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", http_port)) == 0:
                log_lines.append(f"[prepare] HTTP port {http_port} conflict detected, bumping to {http_port + 1}")
                http_port += 1
    except OSError:
        pass

    log_lines.append(f"[prepare] Final ports: TCP={tcp_port} UDP={udp_port} HTTP={http_port}")
    for line in log_lines:
        print(line, file=sys.stderr)

    log_path = os.path.join(cfg_dir, "prepare_debug.log")
    with open(log_path, "w") as f:
        f.write("\n".join(log_lines) + "\n")

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

    wrapper = os.path.join(server_dir, "launch_server.sh")
    debug_log = os.path.join(cfg_dir, "launch_debug.log")
    with open(wrapper, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("#!/bin/bash\n")
        handle.write(f'LOG="{debug_log}"\n')
        handle.write('echo "=== Launch $(date -Iseconds) ===" >> "$LOG"\n')
        handle.write('echo "PID: $$" >> "$LOG"\n')
        handle.write('echo "PWD: $(pwd)" >> "$LOG"\n')
        handle.write('echo "USER: $(whoami)" >> "$LOG"\n')
        handle.write('echo "Ports:" >> "$LOG"\n')
        handle.write(f'echo "  TCP/UDP listener: {tcp_port}" >> "$LOG"\n')
        handle.write(f'echo "  HTTP: {http_port}" >> "$LOG"\n')
        handle.write('echo "Network interfaces:" >> "$LOG"\n')
        handle.write('ip addr show 2>/dev/null | grep "inet " >> "$LOG" 2>&1\n')
        handle.write('echo "Listening ports before launch:" >> "$LOG"\n')
        handle.write(f'ss -tlnp 2>/dev/null | grep -E "{tcp_port}|{http_port}" >> "$LOG" 2>&1\n')
        handle.write(f'ss -ulnp 2>/dev/null | grep "{udp_port}" >> "$LOG" 2>&1\n')
        handle.write('echo "ENV:" >> "$LOG"\n')
        handle.write('env | grep -iE "steam|proton|wine|home|display" >> "$LOG" 2>&1\n')
        handle.write('echo "Launching server..." >> "$LOG"\n')
        handle.write(f'exec "${{0%/*}}/../.proton/proton" runinprefix '
                     f'"${{0%/*}}/AssettoCorsaEVOServer.exe" '
                     f'-serverconfig {launch["serverconfig"]} '
                     f'-seasondefinition {launch["seasondefinition"]}\n')
    os.chmod(wrapper, 0o755)

    print(f"Prepared launch payloads for '{config['server_name']}' on TCP/UDP {tcp_port}, HTTP {http_port}.")


if __name__ == "__main__":
    main()
