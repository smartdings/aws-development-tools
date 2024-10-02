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

- **Python 3.x**
- **AWS CLI**: Configure your AWS credentials and default region.
- **Docker**: Required to run the tunnel in a container.

## Installation

1. Clone this repository or download the script directly:

   ```bash
   git clone https://github.com/smartdings/aws-development-tools.git
   pip install ./aws-development-tools/iot/tunnel
   ```

2. Make the script executable (if needed):

   ```bash
   chmod +x aws_iot_tunnel.py
   ```

## Usage

To use the script, you need to provide your AWS profile, the Thing name, and optionally specify the region and port.

```bash
aws-iot-tunnel --profile <aws_profile> --thing-name <thing_name> [--region <region>] [--port <port>]
```

OR

```bash
./aws_iot_tunnel.py --profile <aws_profile> --thing-name <thing_name> [--region <region>] [--port <port>]
```

### Example

```bash
aws-iot-tunnel --profile myawsprofile --thing-name MyIoTThing --region us-west-2
```

## Command-Line Arguments

| Argument          | Short Form | Type   | Required | Description                                            |
|-------------------|------------|--------|----------|--------------------------------------------------------|
| `--profile`       | `-p`       | string | Yes      | AWS profile to use for authentication.                 |
| `--thing-name`    | `-t`       | string | Yes      | Name of the AWS IoT Thing to connect to.               |
| `--region`        | `-r`       | string | No       | AWS region to use (defaults to the configured region). |
| `--port`          | `-P`       | int    | No       | Port to bind (defaults to `5555`).                     |

## How It Works

1. **AWS CLI Commands**: The script interacts with the AWS IoT Secure Tunneling service using AWS CLI commands to manage tunnels and rotate access tokens.
2. **Docker Integration**: It runs a Docker container configured for the appropriate architecture to establish a secure tunnel to the specified IoT device.
3. **Token Management**: The script checks for existing tunnels and manages the source access tokens required for secure communication.

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
