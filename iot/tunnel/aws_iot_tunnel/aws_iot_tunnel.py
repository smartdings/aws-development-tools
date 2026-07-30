#!/usr/bin/env python3

"""
===============================================================================
Script Name: aws_iot_tunnel.py
Description: This script sets up and manages a secure tunnel to an AWS IoT device.
Usage: ./aws_iot_tunnel.py --thing-name <thing_name> [--port <port>] [--profile <aws_profile>] [--region <region>] [--remove-fingerprint] [--new-tunnel] [--v2]
Requirements:
  - boto3: AWS SDK for Python
  - docker: Docker SDK for running containers
  - Python 3.x
===============================================================================
"""

import argparse
import time
import boto3
import docker
import docker.errors
import subprocess
import sys
import platform
from datetime import datetime, timezone
from typing import Dict, Literal, Optional, Union

# Constants
DEFAULT_SERVICE = "SSH"  # Service type for the tunnel
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5555  # Default port for Docker
DEFAULT_CLIENT_TYPE = "V1"  # Local-proxy protocol version; V2 supports multiple simultaneous sessions per tunnel but requires a V2-capable local proxy on the destination device

# Docker labels used to identify which thing/tunnel a container belongs to,
# independent of the container's (human-readable, but not authoritative) name.
LABEL_THING_NAME = "aws-iot-tunnel.thing-name"
LABEL_TUNNEL_ID = "aws-iot-tunnel.tunnel-id"


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
            - new_tunnel: Boolean flag to force opening a new tunnel instead of reusing an existing one
            - v2: Boolean flag to use the V2 local-proxy protocol instead of the V1 default
    """
    parser = argparse.ArgumentParser(description="Sets up and manages a secure tunnel to an AWS IoT device.")

    parser.add_argument("-t", "--thing-name", type=str, required=True, help="AWS IoT Thing name")
    parser.add_argument("-p", "--profile", type=str, help="AWS profile to use")
    parser.add_argument("-r", "--region", type=str, help="AWS region to use")
    parser.add_argument("-P", "--port", type=int, default=DEFAULT_PORT, help="Port to bind")
    parser.add_argument("-R", "--remove-fingerprint", action="store_true", help="Remove SSH fingerprint")
    parser.add_argument(
        "-N",
        "--new-tunnel",
        action="store_true",
        help="Force opening a new tunnel instead of reusing an existing open tunnel",
    )
    parser.add_argument(
        "-V",
        "--v2",
        action="store_true",
        help="Use the V2 local-proxy protocol instead of the V1 default. V2 supports multiple simultaneous "
        "sessions per tunnel, but requires the destination device's local proxy to also support V2.",
    )

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
        Retrieve an existing open tunnel ID for the specified IoT Thing. If more than one
        open tunnel exists, the user is prompted to choose one, defaulting to the newest.

        Returns:
            Optional[str]: The tunnel ID if an open tunnel is found, otherwise None.

        Raises:
            SystemExit: If an error occurs during tunnel retrieval.
        """
        try:
            response = self.client.list_tunnels(thingName=self.thing_name)
            tunnels = response.get("tunnelSummaries", [])
            open_tunnels = [tunnel for tunnel in tunnels if tunnel.get("status") == "OPEN"]

            if not open_tunnels:
                return None

            if len(open_tunnels) > 1:
                open_tunnels.sort(key=lambda tunnel: tunnel.get("createdAt") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        except Exception as e:
            print(f"Error: Failed to get existing tunnel ID. {e}", file=sys.stderr)
            sys.exit(1)

        if len(open_tunnels) == 1:
            return open_tunnels[0].get("tunnelId")

        return self._select_tunnel(open_tunnels)

    def _select_tunnel(self, open_tunnels: list) -> str:
        """
        Prompt the user to pick which open tunnel to reuse when more than one exists,
        defaulting to the newest tunnel.

        Args:
            open_tunnels (list): Open tunnel summaries, sorted newest-first.

        Returns:
            str: The tunnelId selected by the user (or the newest one by default).
        """
        newest_tunnel_id = open_tunnels[0].get("tunnelId")

        print(f"Found {len(open_tunnels)} open tunnels for '{self.thing_name}':")
        for index, tunnel in enumerate(open_tunnels, start=1):
            suffix = " (newest)" if index == 1 else ""
            print(f"  [{index}] {tunnel.get('tunnelId')} - created {tunnel.get('createdAt')}{suffix}")

        if not sys.stdin.isatty():
            print(f"Non-interactive session detected. Using newest tunnel: {newest_tunnel_id}")
            return newest_tunnel_id

        try:
            choice = input(f"Select a tunnel to reuse [1-{len(open_tunnels)}] (default: 1): ").strip()
        except EOFError:
            choice = ""

        if not choice:
            return newest_tunnel_id

        try:
            index = int(choice)
        except ValueError:
            index = None

        if index is not None and 1 <= index <= len(open_tunnels):
            return open_tunnels[index - 1].get("tunnelId")

        print(f"Invalid selection. Using newest tunnel: {newest_tunnel_id}")
        return newest_tunnel_id

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

    def get_token(self, force_new: bool = False) -> "tuple[str, str]":
        """
        Retrieve the access token for the tunnel, either by finding an existing tunnel or creating a new one.

        Args:
            force_new (bool): If True, skip reusing an existing open tunnel and always open a new one.

        Returns:
            tuple[str, str]: The source access token and the ID of the tunnel it belongs to.

        Raises:
            SystemExit: If no valid access token is retrieved.
        """
        existing_tunnel_id = None if force_new else self._get_existing_tunnel_id()

        if existing_tunnel_id:
            print(f"Found existing tunnel ID: {existing_tunnel_id}")
            client_mode = self._get_access_token_client_mode(existing_tunnel_id)
            print(f"Rotating access tokens for tunnel ID: {existing_tunnel_id} in client mode {client_mode}")
            response = self._rotate_access_tokens(existing_tunnel_id, client_mode)
            tunnel_id = existing_tunnel_id
        else:
            reason = "reuse of existing tunnel disabled" if force_new else "no existing tunnel found"
            print(f"Opening a new tunnel ({reason})...")
            response = self._open_new_tunnel()
            tunnel_id = response.get("tunnelId")

        if not tunnel_id:
            print("Error: Failed to retrieve tunnel ID.", file=sys.stderr)
            sys.exit(1)

        source_access_token = response.get("sourceAccessToken")

        if not source_access_token or source_access_token.lower() == "null":
            print("Error: Failed to retrieve source access token.", file=sys.stderr)
            sys.exit(1)

        print("Source access token obtained successfully.")
        return source_access_token, tunnel_id


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


def docker_pre_check():
    """
    Verifies if Docker is running. Returns a Docker client if successful,
    or exits with an error if not.

    Returns:
        docker.client.DockerClient: Docker client if daemon is accessible.

    """
    try:
        client = docker.from_env()
        client.ping()
        return client
    except docker.errors.DockerException:
        print("Docker is not running or can't connect to the daemon.", file=sys.stderr)
        sys.exit(1)


def container_name_for(thing_name: str, tunnel_id: str) -> str:
    """
    Build the Docker container name for a given thing/tunnel combination.

    Naming includes the tunnel ID (not the port) so that `docker ps` alone
    shows which AWS IoT tunnel a container is proxying, and so that two
    different tunnels to the same thing (e.g. after `--new-tunnel`) never
    collide on the container name even if they happen to reuse a port over
    time. The port isn't part of the name: `docker ps` already shows each
    container's port mapping, and this script only ever runs one local-proxy
    container per tunnel regardless of port (see `run_docker_container`), so
    the tunnel ID alone is a sufficient and more meaningful identity than
    tunnel+port would be.

    Args:
        thing_name (str): The IoT Thing name.
        tunnel_id (str): The AWS IoT tunnel ID the container proxies.

    Returns:
        str: The Docker container name.
    """
    return f"{thing_name}-{tunnel_id}"


def find_container_using_port(client: "docker.client.DockerClient", port: int):
    """
    Find a running container that already has the given host TCP port bound.

    Args:
        client (docker.client.DockerClient): The Docker client.
        port (int): The host port to check.

    Returns:
        Optional[docker.models.containers.Container]: The container bound to
        that port, or None if the port is free.

    Raises:
        SystemExit: If the Docker daemon can't be queried.
    """
    try:
        # Let the Docker daemon filter by port binding instead of pulling and
        # inspecting every running container client-side.
        matches = client.containers.list(filters={"publish": f"{port}/tcp"})
    except docker.errors.DockerException as e:
        print(f"Error: Failed to list Docker containers. {e}", file=sys.stderr)
        sys.exit(1)

    return matches[0] if matches else None


def prompt_yes_no(prompt_text: str) -> bool:
    """
    Ask a y/N question. Defaults to "no", both when the user just presses enter
    and when the session isn't interactive.

    Args:
        prompt_text (str): The prompt to show (used only in interactive sessions).

    Returns:
        bool: True if the user answered yes, False otherwise.
    """
    if not sys.stdin.isatty():
        print("Non-interactive session detected. Defaulting to 'no'.")
        return False

    try:
        choice = input(prompt_text).strip().lower()
    except EOFError:
        choice = ""

    return choice in ("y", "yes")


def resolve_container_conflict(container, description: str) -> None:
    """
    Print `description`, then ask whether to stop the conflicting container.
    Declining leaves it running and aborts the whole run (SystemExit), since
    starting a new container can't safely proceed while it's up.

    Args:
        container (docker.models.containers.Container): The conflicting container.
        description (str): One-line explanation of why it conflicts, shown to the user.

    Raises:
        SystemExit: If the user declines to stop the container, or stopping it fails.
    """
    print(description)
    if not prompt_yes_no(f"Stop container '{container.name}' and continue? [y/N]: "):
        print(f"Leaving container '{container.name}' running. Aborting.", file=sys.stderr)
        sys.exit(1)

    try:
        print(f"Stopping container '{container.name}'...")
        container.stop()
        container.wait()  # Wait for the container to stop
        time.sleep(1)  # wait before starting new container
        print(f"Container '{container.name}' stopped successfully.")
    except docker.errors.NotFound:
        print(f"Container '{container.name}' was already gone. Skipping stop.")
    except docker.errors.DockerException as e:
        print(f"Error stopping container: {e}", file=sys.stderr)
        sys.exit(1)


def run_docker_container(
    region_name: str,
    docker_image: str,
    thing_name: str,
    source_access_token: str,
    port: int,
    tunnel_id: str,
    use_v2: bool = False,
):
    """
    Run a Docker container for the secure tunnel using the Docker SDK.

    Two independent conflicts are checked before starting: another container
    already bound to the requested host port (an OS-level constraint, and may
    belong to any thing/tunnel), and this exact tunnel already having a running
    container regardless of port (this script only ever runs one local-proxy
    process per tunnel). Either one prompts to stop the conflicting container,
    defaulting to "no".

    Args:
        region_name (str): The AWS region.
        docker_image (str): The Docker image to use based on system architecture.
        thing_name (str): The IoT Thing name.
        source_access_token (str): The source access token for the tunnel.
        port (int): The port to expose for the secure tunnel.
        tunnel_id (str): The AWS IoT tunnel ID the container proxies.
        use_v2 (bool): If True, run the local proxy with the V2 destination client type instead of V1.

    Returns:
        None

    Raises:
        SystemExit: If an error occurs while running or stopping the Docker container,
            or if the user chooses not to stop a conflicting container.
    """

    client_type: Literal["V1", "V2"] = "V2" if use_v2 else DEFAULT_CLIENT_TYPE

    client = docker_pre_check()
    container_name = container_name_for(thing_name, tunnel_id)

    port_conflict = find_container_using_port(client, port)
    if port_conflict:
        existing_thing_name = port_conflict.labels.get(LABEL_THING_NAME, "unknown")
        existing_tunnel_id = port_conflict.labels.get(LABEL_TUNNEL_ID, "unknown")
        relation = (
            f"the same thing '{thing_name}'" if existing_thing_name == thing_name else f"a different thing (requested: '{thing_name}')"
        )
        resolve_container_conflict(
            port_conflict,
            f"Port {port} is already in use by container '{port_conflict.name}' (tunnel '{existing_tunnel_id}'), for {relation}.",
        )

    # This script only ever runs one local-proxy container per tunnel, so if this
    # exact tunnel already has a container - even bound to a different port than
    # requested - starting a second one would knock the first offline. Looking it
    # up by container_name also naturally catches (and quietly cleans up) a
    # stopped, not-yet-removed leftover from a previous run, including the one
    # just stopped above if `remove=True` hasn't finished removing it yet.
    try:
        tunnel_container = client.containers.get(container_name)
    except docker.errors.NotFound:
        tunnel_container = None
    except docker.errors.DockerException as e:
        print(f"Error: Failed to inspect existing container '{container_name}'. {e}", file=sys.stderr)
        sys.exit(1)

    if tunnel_container and tunnel_container.status == "running":
        resolve_container_conflict(
            tunnel_container,
            f"Tunnel '{tunnel_id}' for thing '{thing_name}' already has a running container '{tunnel_container.name}'. "
            "This script only runs one local-proxy container per tunnel.",
        )
    elif tunnel_container:
        # Best-effort cleanup: the container isn't running, so it can't be an
        # active conflict. If it's stuck mid-removal (e.g. Docker is still
        # finishing the `remove=True` auto-cleanup from a container we just
        # stopped above), don't fail the whole run over it — let `containers.run()`
        # below sort it out, since it already has its own clear error handling.
        print(f"Removing stale container '{tunnel_container.name}' left over from a previous run...")
        try:
            tunnel_container.remove(force=True)
        except docker.errors.NotFound:
            pass
        except docker.errors.DockerException as e:
            print(f"Warning: Failed to remove stale container '{tunnel_container.name}'. {e}", file=sys.stderr)

    # Run the new Docker container
    try:
        print(f"Starting Docker container '{container_name}' with image '{docker_image}'...")
        client.containers.run(
            image=docker_image,
            name=container_name,
            environment={"AWSIOT_TUNNEL_ACCESS_TOKEN": source_access_token},
            ports={f"{port}/tcp": port},
            labels={LABEL_THING_NAME: thing_name, LABEL_TUNNEL_ID: tunnel_id},
            detach=True,
            remove=True,  # Automatically removes the container when it stops
            command=f"--region {region_name} -b {DEFAULT_HOST} -s {port} -c /etc/ssl/certs --destination-client-type {client_type}",
        )
        print(f"Docker container '{container_name}' started successfully on port {port}.")
    except docker.errors.DockerException as e:
        print(f"Error: Failed to start Docker container: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """Main execution flow: Parse arguments, configure environment, manage tunnel, and start Docker container."""
    args = parse_arguments()
    docker_pre_check()
    docker_image = detect_architecture()

    secure_tunnel = SecureTunnel(args.thing_name, args.port, args.profile, args.region)
    source_access_token, tunnel_id = secure_tunnel.get_token(force_new=args.new_tunnel)
    region_name = secure_tunnel.session.region_name

    run_docker_container(region_name, docker_image, args.thing_name, source_access_token, args.port, tunnel_id, args.v2)  # type: ignore

    if args.remove_fingerprint:
        delete_ssh_fingerprint("localhost", args.port)


if __name__ == "__main__":
    main()
