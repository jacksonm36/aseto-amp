#!/usr/bin/env python3
"""Build compressed -serverconfig / -seasondefinition payloads for AssettoCorsaEVOServer.exe."""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import zlib
from datetime import datetime

BASE = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()


def amplog(component, level, message):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{component} {level}/1]  : {message}")

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

    amplog("Prepare Info", "Info", f"Requested ports: TCP={tcp_port} UDP={udp_port} HTTP={http_port}")

    for check_port in sorted(set([tcp_port, http_port])):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                result = s.connect_ex(("127.0.0.1", check_port))
                if result == 0:
                    amplog("Prepare Warning", "Warning", f"TCP port {check_port} is IN USE")
                else:
                    amplog("Prepare Info", "Info", f"TCP port {check_port} is free")
        except OSError as e:
            amplog("Prepare Error", "Error", f"TCP port {check_port} check failed: {e}")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("0.0.0.0", udp_port))
            amplog("Prepare Info", "Info", f"UDP port {udp_port} is free")
    except OSError as e:
        amplog("Prepare Warning", "Warning", f"UDP port {udp_port} is IN USE: {e}")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", http_port)) == 0:
                amplog("Prepare Warning", "Warning", f"HTTP port {http_port} conflict with AMP webserver, bumping to {http_port + 1}")
                http_port += 1
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        amplog("Network Info", "Info", f"Hostname: {hostname} | Local IP: {local_ip}")
    except Exception:
        pass

    try:
        result = subprocess.run(["ip", "addr", "show"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if "inet " in line:
                amplog("Network Info", "Info", f"Interface: {line.strip()}")
    except Exception:
        pass

    try:
        result = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            amplog("Network Info", "Info", f"Default route: {line.strip()}")
    except Exception:
        pass

    try:
        public_ip = subprocess.run(["curl", "-s", "--connect-timeout", "3", "https://api.ipify.org"],
                                   capture_output=True, text=True, timeout=5).stdout.strip()
        if public_ip:
            amplog("Network Info", "Info", f"Public IP: {public_ip}")
    except Exception:
        pass

    try:
        backend_ip = socket.gethostbyname("c.gk.sd")
        amplog("Network Info", "Info", f"Backend server c.gk.sd resolves to: {backend_ip}")
    except Exception as e:
        amplog("Network Error", "Error", f"Cannot resolve backend c.gk.sd: {e}")

    try:
        result = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if any(str(p) in line for p in [tcp_port, http_port]):
                amplog("Network Warning", "Warning", f"Port already listening: {line.strip()}")
    except Exception:
        pass

    amplog("Prepare Info", "Info", f"Final ports: TCP={tcp_port} UDP={udp_port} HTTP={http_port}")

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
    with open(wrapper, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("#!/bin/bash\n")
        handle.write('amplog() { echo "[$(date +%H:%M:%S)] [$1/$2]  : $3"; }\n')
        handle.write('amplog "Launch Info" "Info" "=== Server Launch ==="\n')
        handle.write('amplog "Launch Info" "Info" "PID: $$ | USER: $(whoami)"\n')
        handle.write('amplog "Launch Info" "Info" "Working directory: $(pwd)"\n')
        handle.write(f'amplog "Launch Info" "Info" "Game ports: TCP/UDP {tcp_port}"\n')
        handle.write(f'amplog "Launch Info" "Info" "HTTP port: {http_port}"\n')
        handle.write('amplog "Network Info" "Info" "Network interfaces:"\n')
        handle.write('ip addr show 2>/dev/null | grep "inet " | while read line; do amplog "Network Info" "Info" "  $line"; done\n')
        handle.write('amplog "Network Info" "Info" "Default route: $(ip route show default 2>/dev/null | head -1)"\n')
        handle.write('amplog "Network Info" "Info" "Checking port availability before bind:"\n')
        handle.write(f'TCP_CHECK=$(ss -tlnp 2>/dev/null | grep ":{tcp_port} ")\n')
        handle.write(f'UDP_CHECK=$(ss -ulnp 2>/dev/null | grep ":{udp_port} ")\n')
        handle.write(f'HTTP_CHECK=$(ss -tlnp 2>/dev/null | grep ":{http_port} ")\n')
        handle.write('[ -n "$TCP_CHECK" ] && amplog "Network Warning" "Warning" "TCP port already bound: $TCP_CHECK" || amplog "Network Info" "Info" "TCP port free"\n')
        handle.write('[ -n "$UDP_CHECK" ] && amplog "Network Warning" "Warning" "UDP port already bound: $UDP_CHECK" || amplog "Network Info" "Info" "UDP port free"\n')
        handle.write('[ -n "$HTTP_CHECK" ] && amplog "Network Warning" "Warning" "HTTP port already bound: $HTTP_CHECK" || amplog "Network Info" "Info" "HTTP port free"\n')
        handle.write('amplog "Network Info" "Info" "NAT/iptables PREROUTING rules:"\n')
        handle.write('/usr/sbin/iptables -t nat -L PREROUTING -n 2>/dev/null | while read line; do amplog "Network Info" "Info" "  $line"; done\n')
        handle.write('amplog "Launch Info" "Info" "Starting AssettoCorsaEVOServer.exe via Proton..."\n')
        handle.write('(\n')
        handle.write('  sleep 5\n')
        handle.write(f'  TCP_AFTER=$(ss -tlnp 2>/dev/null | grep ":{tcp_port} ")\n')
        handle.write(f'  UDP_AFTER=$(ss -ulnp 2>/dev/null | grep ":{udp_port} ")\n')
        handle.write(f'  HTTP_AFTER=$(ss -tlnp 2>/dev/null | grep ":{http_port} ")\n')
        handle.write('  amplog "PostLaunch Info" "Info" "=== Post-launch port status (5s) ==="\n')
        handle.write('  [ -n "$TCP_AFTER" ] && amplog "PostLaunch Info" "Info" "TCP: $TCP_AFTER" || amplog "PostLaunch Warning" "Warning" "TCP NOT listening"\n')
        handle.write('  [ -n "$UDP_AFTER" ] && amplog "PostLaunch Info" "Info" "UDP: $UDP_AFTER" || amplog "PostLaunch Warning" "Warning" "UDP NOT listening"\n')
        handle.write('  [ -n "$HTTP_AFTER" ] && amplog "PostLaunch Info" "Info" "HTTP: $HTTP_AFTER" || amplog "PostLaunch Warning" "Warning" "HTTP NOT listening"\n')
        handle.write(') &\n')
        handle.write(f'exec "${{0%/*}}/../.proton/proton" runinprefix '
                     f'"${{0%/*}}/AssettoCorsaEVOServer.exe" '
                     f'-serverconfig {launch["serverconfig"]} '
                     f'-seasondefinition {launch["seasondefinition"]}\n')
    os.chmod(wrapper, 0o755)

    print(f"Prepared launch payloads for '{config['server_name']}' on TCP/UDP {tcp_port}, HTTP {http_port}.")


if __name__ == "__main__":
    main()
