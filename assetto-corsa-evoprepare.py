#!/usr/bin/env python3
"""Build compressed -serverconfig / -seasondefinition payloads for AssettoCorsaEVOServer.exe."""
import base64
import json
import os
import struct
import sys
import zlib

BASE = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()


def encode_payload(obj):
    data = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    compressor = zlib.compressobj(level=9, wbits=-zlib.MAX_WBITS)
    compressed = compressor.compress(data) + compressor.flush()
    return base64.b64encode(struct.pack(">I", len(data)) + compressed).decode("ascii")


def load_json(path, default=None):
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
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
    except json.JSONDecodeError:
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


def main():
    cfg_dir = os.path.join(BASE, "cfg")
    server_dir = BASE
    os.makedirs(cfg_dir, exist_ok=True)

    settings = load_json(os.path.join(cfg_dir, "server.json"), {})
    season = load_json(os.path.join(cfg_dir, "season.json"), {})

    tcp_port = int(settings.get("server_tcp_listener_port", 9700))
    udp_port = int(settings.get("server_udp_listener_port", tcp_port))
    http_port = int(settings.get("server_http_port", 8081))

    config = {
        "server_tcp_listener_port": tcp_port,
        "server_udp_listener_port": udp_port,
        "server_tcp_internal_port": tcp_port,
        "server_udp_internal_port": udp_port,
        "server_http_port": http_port,
        "server_name": settings.get("server_name", "Assetto Corsa EVO Server - Powered by AMP"),
        "max_players": int(settings.get("max_players", 16)),
        "cycle": bool(settings.get("cycle", True)),
        "allowed_cars_list_full": build_allowed_cars(server_dir),
        "driver_password": settings.get("driver_password", ""),
        "spectator_password": settings.get("spectator_password", ""),
        "admin_password": settings.get("admin_password", ""),
        "type": settings.get("type", "MultiplayerServerListSessionType_RANKED"),
        "entry_list_path": settings.get("entry_list_path", ""),
        "results_path": settings.get("results_path", ""),
    }

    event = season.get("event", {})
    if not event.get("track"):
        discovered = first_practice_event(server_dir)
        if discovered:
            event = discovered
            season["event"] = event

    if not event.get("track"):
        print("ERROR: No track configured and events_practice.json could not be read.", file=sys.stderr)
        sys.exit(1)

    season.setdefault("export_json", False)
    season.setdefault("game_type", "GameModeType_PRACTICE")
    season.setdefault("weather_type", "GameModeSelectionWeatherType_CLEAR")
    season.setdefault("weather_behaviour", "GameModeSelectionWeatherBehaviour_STATIC")
    season.setdefault("initial_grip", "InitialGrip_GREEN")
    season.setdefault("game_config", season.get("game_config") or {"practice_duration": 1200})

    launch = {
        "serverconfig": encode_payload(config),
        "seasondefinition": encode_payload(season),
    }

    with open(os.path.join(cfg_dir, "launch.json"), "w", encoding="utf-8") as handle:
        json.dump(launch, handle, indent=2)

    print(f"Prepared launch payloads for '{config['server_name']}' on TCP/UDP {tcp_port}, HTTP {http_port}.")


if __name__ == "__main__":
    main()
