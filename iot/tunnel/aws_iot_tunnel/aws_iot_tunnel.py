#!/usr/bin/env python3

"""
===============================================================================
Script Name: aws_iot_tunnel.py
Description: This script sets up and manages a secure tunnel to an AWS IoT device.
Usage: ./aws_iot_tunnel.py --thing-name <thing_name> [--port <port>] [--profile <aws_profile>] [--region <region>] [--remove-fingerprint]
Requirements:
  - boto3: AWS SDK for Python
  - docker: Docker SDK for running containers
  - Python 3.x
===============================================================================
"""

import argparse
import boto3
import docker
import docker.errors
import subprocess
import sys
import platform
from typing import Dict, Literal, Optional, Union

# Constants
DEFAULT_SERVICE = "SSH"  # Service type for the tunnel
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5555  # Default port for Docker


def parse_arguments() -> argparse.Namespace:
    """
    Parse and return command-line arguments.

    Returns:
        argparse.Namespace: Parsed command-line arguments with keys:
            - thing_name: AWS IoT Thing name
            - profile: AWS CLI profile to use (optional)
            - region: AWS region to use (optional)
            - port: Port to bind (default: 5555)
            - remove_fingerprint: Boolean flag to remove SSH fingerprint
    """
    parser = argparse.ArgumentParser(description="Sets up and manages a secure tunnel to an AWS IoT device.")

    parser.add_argument("-t", "--thing-name", type=str, required=True, help="AWS IoT Thing name")
    parser.add_argument("-p", "--profile", type=str, help="AWS profile to use")
    parser.add_argument("-r", "--region", type=str, help="AWS region to use")
    parser.add_argument("-P", "--port", type=int, default=DEFAULT_PORT, help="Port to bind")
    parser.add_argument("-R", "--remove-fingerprint", action="store_true", help="Remove SSH fingerprint")

    args = parser.parse_args()
    return args


def get_docker_image(architecture: str) -> str:
    """
    Get the Docker image corresponding to the detected architecture.

    Args:
        architecture (str): The detected architecture.

    Returns:
        str: Docker image URL.

    Raises:
        SystemExit: If the architecture is unsupported.
    """
    architecture_to_image = {
        "x86_64": "public.ecr.aws/aws-iot-securetunneling-localproxy/ubuntu-bin:amd64-latest",
        "arm64": "public.ecr.aws/aws-iot-securetunneling-localproxy/ubuntu-bin:arm64-latest",
        "armv7l": "public.ecr.aws/aws-iot-securetunneling-localproxy/ubuntu-bin:armv7-latest",
    }

    docker_image = architecture_to_image.get(architecture)
    if not docker_image:
        print(f"Error: Unsupported architecture '{architecture}'.", file=sys.stderr)
        sys.exit(1)

    return docker_image


def normalize_windows_architecture(architecture: str) -> str:
    """
    Normalize architecture string for Windows compatibility.

    Args:
        architecture (str): The detected architecture.

    Returns:
        str: Normalized architecture.
    """
    if architecture == "AMD64":
        return "x86_64"
    elif architecture in ["aarch64", "arm64"]:
        return "arm64"
    return architecture


def detect_unix_architecture() -> str:
    """
    Detect architecture using the 'uname' command on Unix-like systems.

    Returns:
        str: Detected architecture.

    Raises:
        SystemExit: If architecture detection fails.
    """
    try:
        architecture = subprocess.check_output(["uname", "-m"]).decode().strip()
        if architecture == "x86_64":
            return "x86_64"
        elif architecture in ["aarch64", "arm64"]:
            return "arm64"
        elif architecture == "armv7l":
            return "armv7l"
    except Exception as e:
        print(f"Error detecting architecture using uname: {e}", file=sys.stderr)
        sys.exit(1)

    return "unknown"  # Fallback if no architecture is detected


def detect_architecture() -> str:
    """
    Detect the system architecture and return the appropriate Docker image.

    Returns:
        str: Docker image appropriate for the system's architecture.
    """
    architecture = platform.machine()
    architecture = normalize_windows_architecture(architecture)

    # Check if detected architecture is already supported
    if architecture in ["x86_64", "arm64", "armv7l"]:
        print(f"Configured Docker image for architecture: {architecture}")
        return get_docker_image(architecture)

    # Fallback to uname command for Unix-like systems
    architecture = detect_unix_architecture()

    # Final check for supported architectures
    return get_docker_image(architecture)


class SecureTunnel:
    """
    A class that manages an AWS IoT secure tunneling session for a specified IoT Thing.
    """

    def __init__(self, thing_name: str, port: int, profile: Optional[str] = None, region: Optional[str] = None) -> None:
        """
        Initialize the SecureTunnel class with IoT Thing name, AWS profile, and region.

        Args:
            thing_name (str): The name of the IoT Thing.
            port (int): Port number to be used.
            profile (Optional[str]): AWS CLI profile name (optional).
            region (Optional[str]): AWS region (optional).

        Raises:
            SystemExit: If an error occurs during AWS session initialization.
        """
        self.thing_name = thing_name
        self.port = port

        try:
            self.session = boto3.Session(profile_name=profile, region_name=region)
            self.client = self.session.client("iotsecuretunneling")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    def _get_existing_tunnel_id(self) -> Optional[str]:
        """
        Retrieve the first existing open tunnel ID for the specified IoT Thing.

        Returns:
            Optional[str]: The tunnel ID if an open tunnel is found, otherwise None.

        Raises:
            SystemExit: If an error occurs during tunnel retrieval.
        """
        try:
            response = self.client.list_tunnels(thingName=self.thing_name)
            tunnels = response.get("tunnelSummaries", [])
            for tunnel in tunnels:
                if tunnel.get("status") == "OPEN":
                    return tunnel.get("tunnelId")
        except Exception as e:
            print(f"Error: Failed to get existing tunnel ID. {e}", file=sys.stderr)
            sys.exit(1)

        return None

    def _get_access_token_client_mode(self, tunnel_id: str) -> Literal["ALL", "SOURCE"]:
        """
        Determine the client mode for the access token based on the destination connection state.

        Args:
            tunnel_id (str): The tunnel ID to describe.

        Returns:
            Literal["ALL", "SOURCE"]: The client mode for the access token.

        Raises:
            SystemExit: If an error occurs while retrieving the tunnel description.
        """
        try:
            response = self.client.describe_tunnel(tunnelId=tunnel_id)
            destination_connection_state = (
                response.get("tunnel", {}).get("destinationConnectionState", {}).get("status")
            )
            if destination_connection_state == "CONNECTED":
                return "SOURCE"
            return "ALL"
        except Exception as e:
            print(f"Error: Failed to get access token client mode. {e}", file=sys.stderr)
            sys.exit(1)

    def _rotate_access_tokens(self, tunnel_id: str, client_mode: Literal["ALL", "SOURCE"]) -> dict:
        """
        Rotate access tokens for an existing tunnel.

        Args:
            tunnel_id (str): The ID of the tunnel for which to rotate tokens.
            client_mode (Literal["ALL", "SOURCE"]): The client mode to use for token rotation.

        Returns:
            dict: The response from the token rotation.

        Raises:
            SystemExit: If an error occurs during token rotation.
        """
        try:
            kwargs: Dict[str, Union[str, object]] = {"tunnelId": tunnel_id, "clientMode": client_mode}
            if client_mode == "ALL":
                kwargs.update(
                    {
                        "destinationConfig": {
                            "thingName": self.thing_name,
                            "services": [
                                DEFAULT_SERVICE,
                            ],
                        }
                    }
                )
            response = self.client.rotate_tunnel_access_token(**kwargs)
            return response
        except Exception as e:
            print(f"Error: Failed to rotate access tokens. {e}", file=sys.stderr)
            sys.exit(1)

    def _open_new_tunnel(self) -> dict:
        """
        Open a new secure tunnel for the specified IoT Thing.

        Returns:
            dict: The response containing the tunnel details.

        Raises:
            SystemExit: If an error occurs while opening the tunnel.
        """
        try:
            response = self.client.open_tunnel(
                destinationConfig={
                    "thingName": self.thing_name,
                    "services": [
                        DEFAULT_SERVICE,
                    ],
                }
            )
            return response
        except Exception as e:
            print(f"Error: Failed to open new tunnel. {e}", file=sys.stderr)
            sys.exit(1)

    def get_token(self) -> str:
        """
        Retrieve the access token for the tunnel, either by finding an existing tunnel or creating a new one.

        Returns:
            str: The source access token for the tunnel.

        Raises:
            SystemExit: If no valid access token is retrieved.
        """
        existing_tunnel_id = self._get_existing_tunnel_id()

        if existing_tunnel_id:
            print(f"Found existing tunnel ID: {existing_tunnel_id}")
            client_mode = self._get_access_token_client_mode(existing_tunnel_id)
            print(f"Rotating access tokens for tunnel ID: {existing_tunnel_id} in client mode {client_mode}")
            response = self._rotate_access_tokens(existing_tunnel_id, client_mode)
        else:
            print("No existing tunnel found. Opening a new tunnel...")
            response = self._open_new_tunnel()

        source_access_token = response.get("sourceAccessToken")

        if not source_access_token or source_access_token.lower() == "null":
            print("Error: Failed to retrieve source access token.", file=sys.stderr)
            sys.exit(1)

        print("Source access token obtained successfully.")
        return source_access_token


def delete_ssh_fingerprint(hostname: str, port: int):
    """
    Deletes the SSH fingerprint for a given hostname and port using ssh-keygen.

    Args:
        hostname (str): The hostname of the server.
        port (int): The port of the server.

    Returns:
        None

    Raises:
        SystemExit: If the fingerprint cannot be deleted.
    """
    try:
        host_with_port = f"[{hostname}]:{port}"
        subprocess.run(["ssh-keygen", "-R", host_with_port], check=True)
        print(f"Deleted SSH fingerprint for {host_with_port} from known_hosts.")
    except subprocess.CalledProcessError as e:
        print(f"Error deleting fingerprint: {e}", file=sys.stderr)


def run_docker_container(region_name: str, docker_image: str, thing_name: str, source_access_token: str, port: int):
    """
    Run a Docker container for the secure tunnel using the Docker SDK.

    Args:
        region_name (str): The AWS region.
        docker_image (str): The Docker image to use based on system architecture.
        thing_name (str): The IoT Thing name (also used as the Docker container name).
        source_access_token (str): The source access token for the tunnel.
        port (int): The port to expose for the secure tunnel.

    Returns:
        None

    Raises:
        SystemExit: If an error occurs while running or stopping the Docker container.
    """
    client = docker.from_env()

    try:
        # Check if the container is already running
        existing_container = client.containers.list(filters={"name": thing_name})
        if existing_container:
            print(f"Container '{thing_name}' is already running. Stopping the container...")
            existing_container[0].stop()
            existing_container[0].wait()
            print(f"Container '{thing_name}' stopped successfully.")
    except docker.errors.DockerException as e:
        print(f"Error checking or stopping container: {e}", file=sys.stderr)
        sys.exit(1)

    # Run the new Docker container
    try:
        print(f"Starting Docker container '{thing_name}' with image '{docker_image}'...")
        client.containers.run(
            image=docker_image,
            name=thing_name,
            environment={"AWSIOT_TUNNEL_ACCESS_TOKEN": source_access_token},
            ports={f"{port}/tcp": port},
            detach=True,
            remove=True,  # Automatically removes the container when it stops
            command=f"--region {region_name} -b {DEFAULT_HOST} -s {port} -c /etc/ssl/certs --destination-client-type V1",
        )
        print(f"Docker container '{thing_name}' started successfully on port {port}.")
    except docker.errors.DockerException as e:
        print(f"Error: Failed to start Docker container: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """Main execution flow: Parse arguments, configure environment, manage tunnel, and start Docker container."""
    args = parse_arguments()
    docker_image = detect_architecture()

    secure_tunnel = SecureTunnel(args.thing_name, args.port, args.profile, args.region)
    source_access_token = secure_tunnel.get_token()
    region_name = secure_tunnel.session.region_name

    run_docker_container(region_name, docker_image, args.thing_name, source_access_token, args.port)  # type: ignore

    if args.remove_fingerprint:
        delete_ssh_fingerprint("localhost", args.port)


if __name__ == "__main__":
    main()
