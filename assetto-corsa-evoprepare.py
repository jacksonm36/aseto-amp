#!/usr/bin/env python3
"""Build compressed -serverconfig / -seasondefinition payloads for AssettoCorsaEVOServer.exe."""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import zlib
from datetime import datetime

BASE = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()


def amplog(component, level, message):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{component}/{level}]  : {message}")

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
    http_port = int(settings.get("server_http_port", 8090))

    amplog("Prepare Info", "Info", "=== PRE-LAUNCH DIAGNOSTICS ===")
    amplog("Prepare Info", "Info", f"Requested ports: TCP={tcp_port} UDP={udp_port} HTTP={http_port}")

    def run_cmd(cmd, timeout=5):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return r.stdout.strip()
        except Exception:
            return ""

    amplog("System Info", "Info", f"Python: {sys.version.split()[0]} | PID: {os.getpid()} | UID: {os.getuid()}")
    amplog("System Info", "Info", f"Working dir: {os.getcwd()}")
    amplog("System Info", "Info", f"Server dir: {server_dir}")

    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        amplog("System Info", "Info", f"Hostname: {hostname} | Resolved IP: {local_ip}")
    except Exception as e:
        amplog("System Warning", "Warning", f"Hostname resolution failed: {e}")

    amplog("Network Info", "Info", "--- Network Interfaces ---")
    for line in run_cmd(["ip", "addr", "show"]).splitlines():
        if "inet " in line or "state " in line:
            amplog("Network Info", "Info", f"  {line.strip()}")

    amplog("Network Info", "Info", "--- Routing ---")
    for line in run_cmd(["ip", "route", "show"]).splitlines():
        amplog("Network Info", "Info", f"  {line.strip()}")

    try:
        public_ip = run_cmd(["curl", "-s", "--connect-timeout", "3", "https://api.ipify.org"])
        amplog("Network Info", "Info", f"Public IP: {public_ip or 'FAILED TO DETECT'}")
    except Exception:
        amplog("Network Warning", "Warning", "Public IP detection failed")

    try:
        backend_ip = socket.gethostbyname("c.gk.sd")
        amplog("Network Info", "Info", f"Backend c.gk.sd resolves to: {backend_ip}")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            result = s.connect_ex((backend_ip, 6990))
            amplog("Network Info", "Info", f"Backend WSS port 6990 reachable: {'YES' if result == 0 else 'NO (code ' + str(result) + ')'}")
    except Exception as e:
        amplog("Network Error", "Error", f"Backend connectivity check failed: {e}")

    amplog("Port Check", "Info", "--- Port Conflict Scan ---")
    all_ports = sorted(set([tcp_port, udp_port, http_port]))
    for p in all_ports:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                r = s.connect_ex(("127.0.0.1", p))
                amplog("Port Check", "Info" if r != 0 else "Warning", f"TCP {p}: {'FREE' if r != 0 else 'IN USE'}")
        except OSError as e:
            amplog("Port Check", "Error", f"TCP {p}: check error ({e})")

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.bind(("0.0.0.0", p))
                amplog("Port Check", "Info", f"UDP {p}: FREE")
        except OSError as e:
            amplog("Port Check", "Warning", f"UDP {p}: IN USE ({e})")

    amplog("Port Check", "Info", "--- All Listening Sockets ---")
    for line in run_cmd(["ss", "-tlnp"]).splitlines():
        if "LISTEN" in line:
            amplog("Port Check", "Info", f"  TCP: {line.strip()}")
    for line in run_cmd(["ss", "-ulnp"]).splitlines():
        if "UNCONN" in line:
            amplog("Port Check", "Info", f"  UDP: {line.strip()}")

    amplog("Port Check", "Info", "--- Established Connections ---")
    for line in run_cmd(["ss", "-tnp"]).splitlines():
        if "ESTAB" in line:
            amplog("Port Check", "Info", f"  {line.strip()}")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", http_port)) == 0:
                amplog("Port Check", "Warning", f"HTTP port {http_port} conflict detected, bumping to {http_port + 1}")
                http_port += 1
    except OSError:
        pass

    amplog("Firewall Info", "Info", "--- iptables NAT rules ---")
    nat_out = run_cmd(["/usr/sbin/iptables", "-t", "nat", "-L", "-n", "-v"])
    if nat_out:
        for line in nat_out.splitlines():
            amplog("Firewall Info", "Info", f"  {line.strip()}")
    else:
        amplog("Firewall Info", "Info", "  No NAT rules / iptables not available")

    amplog("Firewall Info", "Info", "--- iptables FILTER rules ---")
    filter_out = run_cmd(["/usr/sbin/iptables", "-L", "-n", "-v"])
    if filter_out:
        for line in filter_out.splitlines()[:20]:
            amplog("Firewall Info", "Info", f"  {line.strip()}")
    else:
        amplog("Firewall Info", "Info", "  No filter rules / iptables not available")

    amplog("Process Info", "Info", "--- Wine/Proton processes ---")
    for line in run_cmd(["ps", "aux"]).splitlines():
        if any(k in line.lower() for k in ["wine", "proton", "assetto", "steamcmd"]):
            amplog("Process Info", "Info", f"  {line.strip()}")

    amplog("Prepare Info", "Info", f"Final ports: TCP={tcp_port} UDP={udp_port} HTTP={http_port}")
    amplog("Prepare Info", "Info", "=== END DIAGNOSTICS ===")

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
        handle.write('amplog "Launch Info" "Info" "--- BEGIN SERVER OUTPUT ---"\n')
        handle.write(f'"${{0%/*}}/../.proton/proton" runinprefix '
                     f'"${{0%/*}}/AssettoCorsaEVOServer.exe" '
                     f'-serverconfig {launch["serverconfig"]} '
                     f'-seasondefinition {launch["seasondefinition"]} 2>&1 &\n')
        handle.write('SERVER_PID=$!\n')
        handle.write('amplog "Launch Info" "Info" "Server PID: $SERVER_PID"\n')
        handle.write('trap "amplog \\"Launch Info\\" \\"Info\\" \\"Signal received, stopping server...\\"; kill $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null; exit" INT TERM\n')
        handle.write(f'GP={tcp_port}\n')
        handle.write(f'HP={http_port}\n')
        handle.write('check_ports() {\n')
        handle.write('  local LABEL=$1\n')
        handle.write('  if ! kill -0 $SERVER_PID 2>/dev/null; then\n')
        handle.write('    wait $SERVER_PID 2>/dev/null\n')
        handle.write('    EC=$?\n')
        handle.write('    amplog "Monitor Error" "Error" "Server DIED before check: $LABEL (exit code: $EC)"\n')
        handle.write('    amplog "Monitor Info" "Info" "Remaining wine/proton processes:"\n')
        handle.write('    ps aux 2>/dev/null | grep -iE "wine|proton|assetto" | grep -v grep | while read line; do amplog "Monitor Info" "Info" "  $line"; done\n')
        handle.write('    return 1\n')
        handle.write('  fi\n')
        handle.write('  amplog "Monitor Info" "Info" "=== $LABEL ==="\n')
        handle.write('  amplog "Monitor Info" "Info" "Server PID $SERVER_PID: running ($(ps -o rss= -p $SERVER_PID 2>/dev/null || echo ?)KB RSS)"\n')
        handle.write('  TCP_L=$(ss -tlnp 2>/dev/null | grep ":${GP} ")\n')
        handle.write('  UDP_L=$(ss -ulnp 2>/dev/null | grep ":${GP} ")\n')
        handle.write('  HTTP_L=$(ss -tlnp 2>/dev/null | grep ":${HP} ")\n')
        handle.write('  [ -n "$TCP_L" ] && amplog "Monitor Info" "Info" "TCP ${GP}: $TCP_L" || amplog "Monitor Warning" "Warning" "TCP ${GP}: NOT listening"\n')
        handle.write('  [ -n "$UDP_L" ] && amplog "Monitor Info" "Info" "UDP ${GP}: $UDP_L" || amplog "Monitor Warning" "Warning" "UDP ${GP}: NOT listening"\n')
        handle.write('  [ -n "$HTTP_L" ] && amplog "Monitor Info" "Info" "HTTP ${HP}: $HTTP_L" || amplog "Monitor Warning" "Warning" "HTTP ${HP}: NOT listening"\n')
        handle.write('  CONNS=$(ss -tnp 2>/dev/null | grep ":${GP} " | wc -l)\n')
        handle.write('  amplog "Monitor Info" "Info" "Active TCP connections on ${GP}: ${CONNS}"\n')
        handle.write('  ss -tnp 2>/dev/null | grep ":${GP} " | while read line; do amplog "Monitor Info" "Info" "  CONN: $line"; done\n')
        handle.write('  UDP_CONNS=$(ss -unp 2>/dev/null | grep ":${GP} " | wc -l)\n')
        handle.write('  amplog "Monitor Info" "Info" "Active UDP sessions on ${GP}: ${UDP_CONNS}"\n')
        handle.write('  WSS_CONNS=$(ss -tnp 2>/dev/null | grep ":6990 " | wc -l)\n')
        handle.write('  amplog "Monitor Info" "Info" "WebSocket connections to backend (:6990): ${WSS_CONNS}"\n')
        handle.write('  ss -tnp 2>/dev/null | grep ":6990 " | while read line; do amplog "Monitor Info" "Info" "  WSS: $line"; done\n')
        handle.write('}\n')
        handle.write('sleep 3 && check_ports "3s post-launch" || true\n')
        handle.write('sleep 7 && check_ports "10s post-launch" || true\n')
        handle.write('sleep 10 && check_ports "20s post-launch" || true\n')
        handle.write('sleep 30 && check_ports "50s post-launch" || true\n')
        handle.write('sleep 60 && check_ports "110s pre-heartbeat" || true\n')
        handle.write('sleep 30 && check_ports "140s post-heartbeat" || true\n')
        handle.write('wait $SERVER_PID 2>/dev/null\n')
        handle.write('EC=$?\n')
        handle.write('amplog "Launch Info" "Info" "--- END SERVER OUTPUT ---"\n')
        handle.write('amplog "Launch Info" "Info" "Server exited with code: $EC"\n')
        handle.write('exit $EC\n')
    os.chmod(wrapper, 0o755)

    print(f"Prepared launch payloads for '{config['server_name']}' on TCP/UDP {tcp_port}, HTTP {http_port}.")


if __name__ == "__main__":
    main()
