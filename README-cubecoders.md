# `cubecoders-compliant` branch

Reshapes the ACE Generic template toward [CubeCoders/AMPTemplates](https://github.com/CubeCoders/AMPTemplates) rules: **no `.sh` / `.bat` app entrypoint**.

## Why a Python trampoline?

ACE needs fresh zlib+base64 `-serverconfig` / `-seasondefinition` on every Start. AMP does **not** reimport `cfg/launch.json` into `FormattedArgs` after PreStart, so pointing `App.Executable*` at Proton/EXE alone launches with **empty payloads**.

This branch launches **`python3` / `python.exe`** (real executables, same class as `mono` hosts) with:

```text
prepare_launch.py "{{$FullBaseDir}}" --run
```

That builds payloads, then `exec`s Proton (`runinprefix AssettoCorsaEVOServer.exe …`) or the Windows EXE. AMP still monitors `AssettoCorsaEVOServer.exe` via `MonitorChildProcessName` + `DumpFullChildProcessTree`.

## vs `main`

| | `main` | `cubecoders-compliant` |
|--|--------|------------------------|
| App entry | `launch_server.sh` / `.bat` | `python3` / `python.exe` `--run` |
| Extra console | Proton mirror, IP via `ss` | Dropped |
| Version | `1.30` | `2.0.1-compliant` |

## Flags

- `--run` — build + exec game
- `--dry-run` — build only; print intent; no exec
- `--allow-missing` — Update soft-skip if EXE not installed yet

## CubeCoders merge

Their README still rejects AI-generated configs. This branch is for technical fit / ADS use (`jacksonm36/aseto-amp:cubecoders-compliant`), not a guaranteed official merge.
