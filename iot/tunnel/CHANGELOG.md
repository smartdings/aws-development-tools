# Changelog

## [0.11] - 2026-07-30

- Docker containers are now named and labeled after the thing name and the tunnel ID they're proxying (e.g. `MyIoTThing-<tunnel-id>`), so `docker ps`/`docker inspect` show exactly which AWS IoT tunnel each container belongs to. The port isn't part of the identity: `docker ps` already shows each container's port mapping, and an AWS IoT tunnel only ever supports one active local-proxy session at a time regardless of port.
- Replaced the auto-close/error behavior for a busy resource with a single prompt, defaulting to "no": if the requested host port is already bound by another container, or this exact tunnel already has a running container on a different port, the script prints who's holding it and asks whether to stop it. Declining leaves the existing container running and exits without starting a new one. A stopped, not-yet-removed leftover from a previous run is still cleaned up silently. Two different tunnels to the same thing (e.g. via `--new-tunnel`) can run concurrently on different ports without conflict.

## [0.10] - 2026-07-30

- Added `--new-tunnel`/`-N` option to force opening a new tunnel instead of reusing an existing open tunnel.
- When multiple open tunnels exist for a thing, list them and prompt for which to reuse, defaulting to the newest, instead of picking an arbitrary one. The prompt is skipped automatically (defaulting to the newest) when stdin isn't a TTY, and Ctrl-C now aborts instead of silently proceeding.

## [0.9] - 2024-10-10

- handle docker daemon not running exception gracefully.

## [0.8] - 2024-10-08

- fixed existing container not found exception.
- Improved README.md.

## [0.7] - 2024-10-08

- fixed unable to start new container after stopping existing container.
- Improved README.md.

## [0.6] - 2024-10-08

- Normalized windows architecture detection.

## [0.5] - 2024-10-03

- Added LICENSE.
- Improved README.md.

## [0.4] - 2014-10-03

- Better code documentation.
- Added git workflow.

## [0.3] - 2024-10-03

- Support for removing SSH fingerprint.
- Dependency moved to boto3 and docker SDK.

## [0.2] - 2024-10-02

- Initial release.
