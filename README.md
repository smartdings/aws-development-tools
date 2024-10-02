# AWS Development Tools

## Overview

This repository contains a collection of development tools and scripts designed to streamline various AWS-related tasks. Each tool serves a specific purpose and can be used independently or in conjunction with other tools in the repository.

### `iot/tunnel`

The `tunnel` directory contains the **AWS IoT Tunnel** script, which sets up and manages a secure tunnel to an AWS IoT device using the AWS IoT Secure Tunneling feature by checking for existing open tunnels to avoid unnecessary opening of new tunnels. It leverages prebuilt Docker images from [aws-iot-securetunneling-localproxy](https://github.com/aws-samples/aws-iot-securetunneling-localproxy) to create a secure connection, enabling interaction with IoT devices from your local environment. By opening a tunnel on your machine, you can easily use development tools like VSCode, which cannot be utilized via the AWS web UI.

### Usage

For usage instructions related to the script, navigate to the script directory and refer to the `README.md` file located there.

## Contributing

Contributions are welcome! If you would like to add a new tool, enhance existing scripts, or improve documentation, please submit a pull request.

## License

This repository is licensed under the [Apache License 2.0](LICENSE).

### Key Points Covered

- **Overview**: A brief introduction to the purpose of the repository.
- **Contributing**: Encouraging contributions and collaboration.
- **License**: Including licensing information.
