# `cubecoders-compliant` branch notes

See the main project docs in [`README.md`](README.md).

## Intent

Closer to [CubeCoders/AMPTemplates](https://github.com/CubeCoders/AMPTemplates) rules: **no `.sh` / `.bat` as `App.Executable*`**.

## How it differs from `main`

| | `main` | `cubecoders-compliant` |
|--|--------|------------------------|
| App entry | `launch_server.sh` / `.bat` | `/usr/bin/python3` or `py.exe -3` + `prepare_launch.py --run` |
| Console extras | ACE/Proton log mirror, ready poller, best-effort client IP | Dropped |
| Config version | `1.30` | `2.0.2-compliant` |

## Why Python instead of bare Proton?

ACE needs fresh zlib+base64 `-serverconfig` / `-seasondefinition` every Start. AMP does not reimport `cfg/launch.json` into live CLI args after PreStart, so bare Proton/EXE would launch with empty payloads. The Python trampoline is a **real executable** (like `mono` hosts), builds payloads, then `exec`s Proton or the Windows EXE.

## ADS

```text
jacksonm36/aseto-amp:cubecoders-compliant
```

CubeCoders may still reject a PR (AI policy / Steam ownership quirks). This branch is for technical fit and self-hosted ADS use.
