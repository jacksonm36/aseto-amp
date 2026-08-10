# Assetto Corsa EVO — AMP Generic Template

Community [AMP](https://cubecoders.com/AMP) Generic Module template for the **Assetto Corsa EVO** dedicated server (Steam app `4564210`, ownership `3058630`).

- **Repo:** https://github.com/jacksonm36/aseto-amp  
- **ADS prefix:** `ASETO` (see [`manifest.json`](manifest.json))  
- **Min AMP:** 2.6.0.0  

The dedicated server is **Windows-only**. On Linux, AMP runs it through **Proton GE** inside the debian container.

---

## Branches

| Branch | Use when | App entrypoint |
|--------|----------|----------------|
| [`main`](https://github.com/jacksonm36/aseto-amp/tree/main) | Production / full AMP UX | `launch_server.sh` / `.bat` (Proton wrapper, console log mirror, ready signal, best-effort client IP) |
| [`cubecoders-compliant`](https://github.com/jacksonm36/aseto-amp/tree/cubecoders-compliant) | Closer to CubeCoders “no `.sh`/`.bat` app entry” rule | `python3` / `py.exe` → `prepare_launch.py --run` (builds payloads, then `exec`s Proton/EXE) |
| [`development`](https://github.com/jacksonm36/aseto-amp/tree/development) | Staging for `main` | Same lineage as `main` |
| [`fallback`](https://github.com/jacksonm36/aseto-amp/tree/fallback) | Older stable pin | Legacy |

**Recommended for most hosts:** `main`.

Branch-specific notes for the compliant variant: [`README-cubecoders.md`](README-cubecoders.md).

---

## Add the template in AMP (ADS)

1. Open **Configuration → Instance Deployment**.
2. **Add** a Configuration Repository:
   - `jacksonm36/aseto-amp:main`  
   - or `jacksonm36/aseto-amp:cubecoders-compliant`
3. **Fetch**, refresh the browser.
4. Create / redeploy an instance using the **ASETO** Assetto Corsa EVO template.

Existing instances: point FetchURLs / GenericModule template at the branch you want, then **Update**, then **Start**.

---

## Requirements

- AMP with Generic Module support (ADS or standalone).
- Steam account that **owns Assetto Corsa EVO** (app `3058630`). Anonymous SteamCMD returns *No subscription* for `4564210`.
- **Linux:** `python3` (ExtraContainerPackages already includes it), network for Proton GE download.
- **Windows (`cubecoders-compliant`):** `py.exe` / Python 3 on PATH for the trampoline.
- Firewall / NAT: see [Ports](#ports--networking).

---

## Ports & networking

| Port | Protocol | Purpose |
|------|----------|---------|
| **Game port** (default **9700**) | **TCP + UDP** (same number) | Players / Kunos multiplayer path |
| **HTTP/API** (default **8081**, often **8090** on older instances) | TCP | Optional local listing/results API — **not** the lobby |
| AMP web UI (e.g. 8084) | TCP | Panel only — do **not** expose for “gaming” |

**Gaming-only WAN rules:** forward **9700 TCP and UDP** to the AMP host. Nothing else is required for players.

### Outbound static-port NAT

Kunos backend heartbeats / reachability often break if the firewall **randomizes** the source port on outbound UDP. On OPNsense/pfSense, add an outbound NAT rule for the ACE host with **Static Port = YES** so `host:9700` stays `WAN_IP:9700`.

Lobby registration uses outbound WSS (e.g. `c.gk.sd:6990`) — no extra inbound port for that.

---

## How Start works

ACE does not take plain JSON on the CLI. It expects **zlib-compressed, length-prefixed, base64** payloads:

```text
AssettoCorsaEVOServer.exe -serverconfig <payload> -seasondefinition <payload>
```

[`assetto-corsa-evoprepare.py`](assetto-corsa-evoprepare.py) (installed as `prepare_launch.py`) reads `cfg/server.json` + `cfg/season.json`, validates track/layout against `events_*.json`, and writes `cfg/launch.json`.

### `main` branch

1. PreStart runs prepare → writes payloads **and** regenerates `launch_server.sh` / `.bat`.
2. AMP starts the wrapper; wrapper runs Proton/`runinprefix`, tails the Wine ACE log into the console, emits AMP ready lines, optional client IP heuristics.

### `cubecoders-compliant` branch

AMP does **not** reliably reimport `launch.json` into `FormattedArgs` after PreStart, so bare Proton/EXE would start with empty payloads.

1. PreStart validates payloads (prepare without `--run`).
2. App entry is Python: `prepare_launch.py … --run` builds payloads, then **`exec`s** Proton or the Windows EXE.
3. AMP still monitors `AssettoCorsaEVOServer.exe` (`DumpFullChildProcessTree=True`).

Prepare flags:

| Flag | Meaning |
|------|---------|
| `--run` | Build payloads and exec the game |
| `--dry-run` | Build only; skip exec |
| `--allow-missing` | Soft-skip if the server EXE is not installed yet (Update) |

---

## Configuration overview

Settings live under AMP **Configuration** and map into:

| File | Role |
|------|------|
| `cfg/server.json` | Name, ports, passwords, cycle, HTTP API, etc. |
| `cfg/season.json` | Track / layout / event, weather, practice duration |
| `cfg/launch.json` | Generated payloads (do not hand-edit) |

Track IDs must match the server catalog (`events_practice.json`, etc.), e.g. `Nurburgring` + `Nordschleife`. Legacy AC1 ids like `ks_nordschleife` are aliased when possible.

**HTTP/API:** toggle Enable HTTP/API; set port to `0` to disable. This is unrelated to Kunos lobby listing.

---

## Repository layout

```text
assetto-corsa-evo.kvp              # GenericModule root
assetto-corsa-evoconfig.json       # Settings manifest
assetto-corsa-evometaconfig.json   # JSON config file mappings
assetto-corsa-evoports.json        # Port definitions
assetto-corsa-evoupdates.json      # SteamCMD, Proton GE, FetchURLs
assetto-corsa-evostart.json        # PreStart stages
assetto-corsa-evoserver.json       # Default server.json seed
assetto-corsa-evoseason.json       # Default season.json seed
assetto-corsa-evoprepare.py        # Payload builder (+ --run on compliant)
manifest.json                      # ADS repository metadata
README-cubecoders.md               # Compliant-branch notes
```

---

## Operations checklist

1. Create instance from this repo branch.
2. **Update** and complete Steam login (owning account).
3. Set track/layout to catalog values; set game port (TCP=UDP).
4. Forward **game port TCP+UDP**; enable **static-port outbound NAT** for the host.
5. **Start**; wait for ready (`Listening to … UDP`) and backend register/heartbeat success.
6. Confirm players can join via in-game multiplayer list.

### Useful console signals

- `Listening to TCP …` / `Listening to UDP …` — sockets up  
- `RegisterRequest` / `MultiplayerServerListRequestRegisterServer (Success: true)` — lobby OK  
- `BackendHeartbeat (Success: true)` — UDP path OK  
- `WeatherNoiseData` / `WeatherEngineData` handler missing — usually harmless ACE noise  
- `connecting gamecar … (name | SteamID)` / `despawning …` — player join/leave (AMP user list regexes)

---

## Limitations

- **Cannot** run ACE “exactly like” stock CubeCoders Proton templates with **no** helper: payloads must be built at Start.
- CubeCoders’ public `AMPTemplates` repo **rejects AI-generated configs**; this repo is for ADS / self-host, not a guaranteed official merge.
- Client **IP** is not logged by ACE; `main` may approximate from TCP peers. Steam APIs do not expose join IPs.
- HTTP API ≠ lobby. Disabling HTTP does not remove the server from Kunos multiplayer.

---

## License / ownership

Community template. Assetto Corsa EVO is property of Kunos Simulazioni / 505 Games. You need a legitimate Steam copy to download the dedicated server files.
