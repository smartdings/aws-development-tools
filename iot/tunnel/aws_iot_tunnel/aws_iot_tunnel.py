#!/usr/bin/env python3

"""
===============================================================================
Script Name: aws_iot_tunnel.py
Description: Sets up and manages a secure tunnel to an AWS IoT device.
Usage: ./aws_iot_tunnel.py --thing-name <thing_name> [--port <port>] [--profile <aws_profile>] [--region <region>]
Requirements:
  - boto3
  - docker
  - Python 3.x
===============================================================================
"""

import argparse
import boto3
import docker
import docker.errors
import sys
import os
import platform
from typing import Optional

# Constants
DEFAULT_SERVICE = "SSH"  # Service type for the tunnel
DEFAULT_PORT = 5555  # Default port for Docker


def parse_arguments() -> argparse.Namespace:
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(description="Sets up and manages a secure tunnel to an AWS IoT device.")

    parser.add_argument("-t", "--thing-name", type=str, required=True, help="AWS IoT Thing name")
    parser.add_argument("-p", "--profile", type=str, help="AWS profile to use")
    parser.add_argument("-r", "--region", type=str, help="AWS region to use")
    parser.add_argument("-P", "--port", type=int, default=DEFAULT_PORT, help="Port to bind")

    args = parser.parse_args()
    return args


def configure_environment() -> str:
    """
    Configure system settings based on architecture.
    Returns the appropriate Docker image based on system architecture.
    """
    architecture_to_image = {
        "x86_64": "public.ecr.aws/aws-iot-securetunneling-localproxy/ubuntu-bin:amd64-latest",
        "arm64": "public.ecr.aws/aws-iot-securetunneling-localproxy/ubuntu-bin:arm64-latest",
        "armv7l": "public.ecr.aws/aws-iot-securetunneling-localproxy/ubuntu-bin:armv7-latest",
    }

    architecture = platform.machine()
    docker_image = architecture_to_image.get(architecture)

    if not docker_image:
        print(f"Error: Unsupported architecture '{architecture}'.", file=sys.stderr)
        sys.exit(1)

    print(f"Configured Docker image for architecture: {architecture}")
    return docker_image


class SecureTunnel:
    def __init__(self, thing_name: str, port: int, profile: Optional[str] = None, region: Optional[str] = None) -> None:
        self.thing_name = thing_name
        self.port = port

        if profile:
            os.environ["AWS_PROFILE"] = profile
        if region:
            os.environ["AWS_REGION"] = region

        self.session = boto3.Session()
        self.client = self.session.client("iotsecuretunneling")

    def get_region_name(self) -> str:
        """
        Retrieve and returns the AWS region name.
        """
        region_name = self.session.region_name
        if not region_name:
            print("Error: Failed to get AWS region name.", file=sys.stderr)
            sys.exit(1)
        return region_name

    def _get_existing_tunnel_id(self) -> Optional[str]:
        """
        Retrieve the first existing OPEN tunnel ID for the specified IoT Thing.
        Returns the tunnel ID if found, else None.
        """
        try:
            response = self.client.list_tunnels(thingName=self.thing_name)
            tunnels = response.get("tunnelSummaries", [])
            for tunnel in tunnels:
                if tunnel.get("status") == "OPEN":
                    return tunnel.get("tunnelId")
        except:
            print("Error: Failed to get existing tunnel id.", file=sys.stderr)
            sys.exit(1)
        return None

    def _rotate_access_tokens(self, tunnel_id: str) -> dict:
        """
        Rotate the source access token for an existing tunnel.
        Returns the response.
        """
        try:
            response = self.client.rotate_tunnel_access_token(
                tunnelId=tunnel_id,
                clientMode="ALL",
                destinationConfig={
                    "thingName": self.thing_name,
                    "services": [
                        DEFAULT_SERVICE,
                    ],
                },
            )
            return response
        except:
            print("Error: Failed to rotate access tokens.", file=sys.stderr)
            sys.exit(1)

    def _open_new_tunnel(self) -> dict:
        """
        Open a new tunnel for the specified IoT Thing.
        Returns the response.
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
        except:
            print("Error: Failed to open new tunnel.", file=sys.stderr)
            sys.exit(1)

    def get_token(self) -> str:
        """
        Manage the tunnel by retrieving an existing one or creating a new one.
        Returns the source access token.
        """
        existing_tunnel_id = self._get_existing_tunnel_id()

        if existing_tunnel_id:
            print(f"Found existing tunnel ID: {existing_tunnel_id}")
            print(f"Rotating access tokens for tunnel ID: {existing_tunnel_id}")
            response = self._rotate_access_tokens(existing_tunnel_id)
        else:
            print("No existing tunnel found. Opening a new tunnel...")
            response = self._open_new_tunnel()

        source_access_token = response.get("sourceAccessToken")

        # Validate source access token
        if not source_access_token or source_access_token.lower() == "null":
            print("Error: Failed to retrieve source access token.", file=sys.stderr)
            sys.exit(1)

        print("Source access token obtained successfully.")
        return source_access_token


def run_docker_container(region_name: str, docker_image: str, thing_name: str, source_access_token: str, port: int):
    """
    Run the Docker container for the tunnel using the Docker SDK.
    Stops the container if it's already running before starting a new one.
    """
    client = docker.from_env()

    # Check if the container is already running
    try:
        existing_container = client.containers.list(filters={"name": thing_name})
        if existing_container:
            print(f"Container '{thing_name}' is already running. Stopping the container...")
            existing_container[0].stop()
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
            command=f"--region {region_name} -b 0.0.0.0 -s {port} -c /etc/ssl/certs --destination-client-type V1",
        )
        print(f"Docker container '{thing_name}' started successfully on port {port}.")
    except docker.errors.DockerException as e:
        print(f"Error: Failed to start Docker container: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """Main execution flow."""
    args = parse_arguments()
    docker_image = configure_environment()
    secure_tunnel = SecureTunnel(args.thing_name, args.port, args.profile, args.region)
    source_access_token = secure_tunnel.get_token()
    region_name = secure_tunnel.get_region_name()
    run_docker_container(region_name, docker_image, args.thing_name, source_access_token, args.port)


if __name__ == "__main__":
    main()
