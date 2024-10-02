#!/usr/bin/env python3

"""
===============================================================================
Script Name: aws_iot_tunnel.py
Description: Sets up and manages a secure tunnel to an AWS IoT device.
Usage: ./aws_iot_tunnel.py --profile <aws_profile> --thing-name <thing_name> [--region <region>] [--port <port>]
Requirements:
  - boto3
  - Docker
  - Python 3.x
===============================================================================
"""

import argparse
import subprocess
import sys
import json
import platform
import shlex
from typing import List, Optional

# Constants
SERVICE_TYPE = "SSH"  # Service type for the tunnel
DEFAULT_PORT = 5555  # Default port for Docker


def run_aws_cli_command(command_list: List[str]) -> str:
    """
    Run an AWS CLI command and return the output.
    Exits the script if the command fails.
    """
    try:
        result = subprocess.run(command_list, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.stdout.decode("utf-8")
    except subprocess.CalledProcessError as e:
        error_message = e.stderr.decode("utf-8").strip()
        print(f"Error executing command: {' '.join(command_list)}\n{error_message}", file=sys.stderr)
        sys.exit(1)


def get_default_region(profile_name: Optional[str] = None) -> str:
    """
    Get the default AWS region for the specified profile.
    """
    command = f"aws configure get region --profile {profile_name}" if profile_name else "aws configure get region"
    command_list = shlex.split(command)
    return run_aws_cli_command(command_list).strip()


def parse_arguments() -> argparse.Namespace:
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(description="Sets up and manages a secure tunnel to an AWS IoT device.")
    parser.add_argument("-p", "--profile", type=str, required=True, help="AWS profile to use")
    parser.add_argument("-t", "--thing-name", type=str, required=True, help="AWS IoT Thing name")
    parser.add_argument("-r", "--region", type=str, help="AWS region to use")
    parser.add_argument("-P", "--port", type=int, default=DEFAULT_PORT, help="Port to bind")

    args = parser.parse_args()

    # Set default region if not provided
    if not args.region:
        args.region = get_default_region(args.profile)

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
    def __init__(self, profile: str, thing_name: str, region: str, port: int = DEFAULT_PORT) -> None:
        self.profile = profile
        self.thing_name = thing_name
        self.region = region
        self.port = port

    def _get_existing_tunnel_id(self) -> Optional[str]:
        """
        Retrieve the first existing OPEN tunnel ID for the specified IoT Thing.
        Returns the tunnel ID if found, else None.
        """
        command = f"""
        aws iotsecuretunneling list-tunnels
        --thing-name {self.thing_name} \
        --region {self.region} \
        --profile {self.profile}
        """

        command_list = shlex.split(command)
        response = run_aws_cli_command(command_list)
        try:
            tunnels = json.loads(response)
            for tunnel in tunnels.get("tunnelSummaries", []):
                if tunnel.get("status") == "OPEN":
                    return tunnel.get("tunnelId")
        except json.JSONDecodeError:
            print("Error: Failed to parse JSON response from AWS CLI.", file=sys.stderr)
            sys.exit(1)
        return None

    def _rotate_source_access_token(self, tunnel_id: str) -> dict:
        """
        Rotate the source access token for an existing tunnel.
        Returns the JSON response from AWS CLI.
        """
        command = f"""
        aws iotsecuretunneling rotate-tunnel-access-token \
        --tunnel-id {tunnel_id} \
        --client-mode ALL \
        --destination-config thingName={self.thing_name},services={SERVICE_TYPE} \
        --region {self.region} \
        --profile {self.profile}
        """

        command_list = shlex.split(command)
        response = run_aws_cli_command(command_list)
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            print("Error: Failed to parse JSON response from AWS CLI.", file=sys.stderr)
            sys.exit(1)

    def _open_new_tunnel(self) -> dict:
        """
        Open a new tunnel for the specified IoT Thing.
        Returns the JSON response from AWS CLI.
        """
        command = f"""
        aws iotsecuretunneling open-tunnel \
        --destination-config thingName={self.thing_name},services={SERVICE_TYPE} \
        --region {self.region} \
        --profile {self.profile}
        """

        command_list = shlex.split(command)
        response = run_aws_cli_command(command_list)
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            print("Error: Failed to parse JSON response from AWS CLI.", file=sys.stderr)
            sys.exit(1)

    def get_token(self) -> str:
        """
        Manage the tunnel by retrieving an existing one or creating a new one.
        Returns the source access token.
        """
        existing_tunnel_id = self._get_existing_tunnel_id()

        if existing_tunnel_id:
            print(f"Found existing tunnel ID: {existing_tunnel_id}")
            print(f"Rotating source access token for tunnel ID: {existing_tunnel_id}")
            response = self._rotate_source_access_token(existing_tunnel_id)
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


def run_docker_container(region: str, docker_image: str, thing_name: str, source_access_token: str, port: int):
    """
    Run the Docker container for the tunnel.
    Stops the container if it's already running before starting a new one.
    """
    # Check if the container is already running
    try:
        running_containers = (
            subprocess.check_output(["docker", "ps", "-q", "-f", f"name={thing_name}"]).decode().strip()
        )
        if running_containers:
            print(f"Container '{thing_name}' is already running. Stopping the container...")
            subprocess.run(["docker", "stop", thing_name], check=True)
            print(f"Container '{thing_name}' stopped successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Error checking or stopping container: {e}", file=sys.stderr)
        sys.exit(1)

    # Run the new Docker container
    docker_command = f"""
    docker run --rm -d --name {thing_name} \
    -e AWSIOT_TUNNEL_ACCESS_TOKEN={source_access_token} \
    -p {port}:{port} \
    {docker_image} \
    --region {region} \
    -b 0.0.0.0 \
    -s {port} \
    -c /etc/ssl/certs \
    --destination-client-type V1
    """

    docker_command_list = shlex.split(docker_command)

    try:
        subprocess.run(docker_command_list, check=True)
        print(f"Docker container '{thing_name}' started successfully on port {port}.")
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to start Docker container: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """Main execution flow."""
    args = parse_arguments()
    docker_image = configure_environment()
    secure_tunnel = SecureTunnel(args.profile, args.thing_name, args.region, args.port)
    source_access_token = secure_tunnel.get_token()
    run_docker_container(args.region, docker_image, args.thing_name, source_access_token, args.port)


if __name__ == "__main__":
    main()
