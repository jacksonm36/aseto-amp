# `cubecoders-compliant` branch

This branch reshapes the Assetto Corsa EVO Generic template toward [CubeCoders/AMPTemplates](https://github.com/CubeCoders/AMPTemplates) technical rules.

## What changed vs `main`

| Area | `main` | `cubecoders-compliant` |
|------|--------|------------------------|
| App entrypoint | `launch_server.sh` / `.bat` | `AssettoCorsaEVOServer.exe` / `.proton/proton` |
| PreStart | bash/cmd → prepare (writes wrappers + payloads) | `python3` / `python.exe` → prepare (**payloads only**) |
| Console extras | Proton log mirror, ready poller, client IP via `ss` | Dropped (wrapper-only) |
| Config version | `1.30` | `2.0.0-compliant` |

## Still required (ACE reality)

ACE needs zlib+base64 `-serverconfig` / `-seasondefinition` on the command line. PreStart runs **`python3` as an Executable stage** (same helper class as official Proton-GE bash installers) to write `cfg/launch.json`, which AMP maps into CLI args.

## Not a guarantee of CubeCoders merge

Their README still rejects AI-generated configs and prefers a minimal file set. This branch maximizes **technical** fit for a human-reviewed draft PR; it does not claim official acceptance.

## ADS usage

Point the configuration repository at:

`jacksonm36/aseto-amp:cubecoders-compliant`

FetchURLs in this branch already pull prepare/defaults from the `cubecoders-compliant` raw paths.
