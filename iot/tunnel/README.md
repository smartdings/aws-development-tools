# AWS IoT Tunnel Script

## Overview

The `aws_iot_tunnel.py` script sets up and manages a secure tunnel to an AWS IoT device using the AWS IoT Secure Tunneling feature by checking for existing open tunnels to avoid unnecessary opening of new tunnels. It leverages prebuilt Docker images from [aws-iot-securetunneling-localproxy](https://github.com/aws-samples/aws-iot-securetunneling-localproxy) to create a secure connection, enabling interaction with IoT devices from your local environment. By opening a tunnel on your machine, you can easily use development tools like VSCode, which cannot be utilized via the AWS web UI.

## Table of Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Command-Line Arguments](#command-line-arguments)
- [How It Works](#how-it-works)
- [License](#license)

## Requirements

Before running the script, ensure you have the following installed:

- **[Python 3.x](https://www.python.org/downloads/)**
- **[AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-getting-started.html) ([Configured](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-quickstart.html))**
- **[Docker](https://www.docker.com/get-started/)**: Required to run the tunnel in a container.
- **[boto3](https://pypi.org/project/boto3/) (AWS SDK)**: To manage secure tunneling (automatically installed with pip).
- **[docker](https://pypi.org/project/docker/) (Docker SDK)**: To manage docker container (automatically installed with pip).

## Installation

Download the script directly or clone this repository:

   ```bash
   pip install aws-iot-tunnel
   ```

OR

   ```bash
   git clone https://github.com/smartdings/aws-development-tools.git
   pip install ./aws-development-tools/iot/tunnel
   ```

## Usage

1. Run the docker container.

   ```bash
   aws-iot-tunnel -t MyIoTThing -p myawsprofile -r us-west-2
   ```

2. Connect to the iot thing using SSH.

   ```bash
   ssh user@localhost -p 5555
   ```

## Command-Line Arguments

| Argument               | Short Form | Type   | Required | Description                                             |
|------------------------|------------|--------|----------|---------------------------------------------------------|
| `--thing-name`         | `-t`       | string | Yes      | Name of the AWS IoT Thing to connect to.                |
| `--port`               | `-P`       | int    | No       | Port to bind (defaults to `5555`).                      |
| `--profile`            | `-p`       | string | No       | AWS profile to use for authentication.                  |
| `--region`             | `-r`       | string | No       | AWS region to use (defaults to the configured region).  |
| `--remove-fingerprint` | `-R`       |        | No       | Remove SSH fingerprint on localhost with specified port.|
| `--new-tunnel`         | `-N`       |        | No       | Open a brand-new tunnel and leave any existing open tunnel(s) for the thing untouched (they are not closed).|

## How It Works

1. **boto3 SDK**: The script interacts with the AWS IoT Secure Tunneling service using boto3 SDK to manage tunnels and rotate access tokens.
2. **Docker Integration**: It runs a Docker container configured for the appropriate architecture to establish a secure tunnel to the specified IoT device.
3. **Token Management**: The script checks for existing tunnels and manages the source access tokens required for secure communication. AWS IoT Secure Tunneling allows multiple tunnels to stay open for the same thing at once. If more than one open tunnel is found (e.g. after using `--new-tunnel`), the script lists them and prompts you to pick one to reuse, defaulting to the most recently created tunnel.
4. **Explicit New Tunnels**: Use `--new-tunnel`/`-N` to always open a fresh tunnel instead of reusing an existing one. The previous tunnel(s) are left open (not closed) — the IoT device automatically reconnects its local proxy to whichever tunnel was opened most recently, so older tunnels become inactive but still count toward your account's tunnel quota until they expire.
5. **Docker Container Naming and Conflicts**: Each Docker container is named after the thing name and the tunnel ID it's proxying (e.g. `MyIoTThing-<tunnel-id>`) and labeled with both, so `docker ps`/`docker inspect` tell you exactly which AWS IoT tunnel each container belongs to. The port isn't part of the name — `docker ps` already shows each container's port mapping, and an AWS IoT tunnel only ever supports one active local-proxy session at a time regardless of port. Two conflicts are checked before starting: (a) another container already bound to the requested host port (could be any thing/tunnel), and (b) this exact tunnel already having a running container on a *different* port (since only one session per tunnel can be active). Either one prints who's holding it and asks whether to stop it, defaulting to **no**; declining leaves it running and the script exits without starting a new one. In a non-interactive session the prompt is skipped and defaults to "no" automatically. A stopped, not-yet-removed container from a previous run is cleaned up silently (no prompt) since it isn't actually in use. Multiple simultaneous tunnels to the same thing are still fully supported — each one gets a different tunnel ID (and can use a different port).

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

### Key Features of the README

- **Overview**: A brief introduction to what the script does.
- **Requirements**: Specifies what is needed to run the script.
- **Installation**: Instructions on how to set up the script.
- **Usage**: Clear command examples for users to follow.
- **Command-Line Arguments**: A table detailing each argument, its type, and whether it's required.
- **How It Works**: A high-level explanation of the script's functionality.
- **License**: Information about the licensing of the project.
