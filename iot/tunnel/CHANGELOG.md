# Changelog

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
