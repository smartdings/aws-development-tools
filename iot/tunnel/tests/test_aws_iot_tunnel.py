"""Unit tests for aws_iot_tunnel.aws_iot_tunnel."""

import subprocess
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import docker.errors
import pytest

from aws_iot_tunnel import aws_iot_tunnel as tunnel


def _input_raises_eof(prompt=""):
    raise EOFError


# ---------------------------------------------------------------------------
# parse_arguments
# ---------------------------------------------------------------------------


class TestParseArguments:
    def test_defaults(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["aws-iot-tunnel", "-t", "MyThing"])
        args = tunnel.parse_arguments()

        assert args.thing_name == "MyThing"
        assert args.profile is None
        assert args.region is None
        assert args.port == tunnel.DEFAULT_PORT
        assert args.remove_fingerprint is False
        assert args.new_tunnel is False
        assert args.v2 is False

    def test_all_options_long_form(self, monkeypatch):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "aws-iot-tunnel",
                "--thing-name",
                "MyThing",
                "--profile",
                "myprofile",
                "--region",
                "us-west-2",
                "--port",
                "6666",
                "--remove-fingerprint",
                "--new-tunnel",
                "--v2",
            ],
        )
        args = tunnel.parse_arguments()

        assert args.thing_name == "MyThing"
        assert args.profile == "myprofile"
        assert args.region == "us-west-2"
        assert args.port == 6666
        assert args.remove_fingerprint is True
        assert args.new_tunnel is True
        assert args.v2 is True

    def test_all_options_short_form(self, monkeypatch):
        monkeypatch.setattr(
            sys,
            "argv",
            ["aws-iot-tunnel", "-t", "MyThing", "-p", "myprofile", "-r", "us-west-2", "-P", "7777", "-R", "-N", "-V"],
        )
        args = tunnel.parse_arguments()

        assert args.port == 7777
        assert args.remove_fingerprint is True
        assert args.new_tunnel is True
        assert args.v2 is True

    def test_missing_required_thing_name_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["aws-iot-tunnel"])

        with pytest.raises(SystemExit):
            tunnel.parse_arguments()

    def test_negative_port_still_parses(self, monkeypatch):
        # Regression test: a short option string that looks like a negative number
        # (e.g. "-2") makes argparse treat every "-<digits>" token as an option
        # rather than a value, breaking "-P -1". Guards against reintroducing that.
        monkeypatch.setattr(sys, "argv", ["aws-iot-tunnel", "-t", "MyThing", "-P", "-1"])
        args = tunnel.parse_arguments()

        assert args.port == -1


# ---------------------------------------------------------------------------
# get_docker_image / architecture detection
# ---------------------------------------------------------------------------


class TestGetDockerImage:
    @pytest.mark.parametrize(
        "architecture,expected_image",
        [
            ("x86_64", "public.ecr.aws/aws-iot-securetunneling-localproxy/ubuntu-bin:amd64-latest"),
            ("arm64", "public.ecr.aws/aws-iot-securetunneling-localproxy/ubuntu-bin:arm64-latest"),
            ("armv7l", "public.ecr.aws/aws-iot-securetunneling-localproxy/ubuntu-bin:armv7-latest"),
        ],
    )
    def test_supported_architectures(self, architecture, expected_image):
        assert tunnel.get_docker_image(architecture) == expected_image

    def test_unsupported_architecture_exits(self, capsys):
        with pytest.raises(SystemExit):
            tunnel.get_docker_image("sparc")

        assert "Unsupported architecture" in capsys.readouterr().err


class TestNormalizeWindowsArchitecture:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("AMD64", "x86_64"),
            ("aarch64", "arm64"),
            ("arm64", "arm64"),
            ("armv7l", "armv7l"),
        ],
    )
    def test_normalization(self, raw, expected):
        assert tunnel.normalize_windows_architecture(raw) == expected


class TestDetectUnixArchitecture:
    @pytest.mark.parametrize(
        "uname_output,expected",
        [
            (b"x86_64\n", "x86_64"),
            (b"aarch64\n", "arm64"),
            (b"arm64\n", "arm64"),
            (b"armv7l\n", "armv7l"),
        ],
    )
    def test_known_architectures(self, monkeypatch, uname_output, expected):
        monkeypatch.setattr(subprocess, "check_output", MagicMock(return_value=uname_output))
        assert tunnel.detect_unix_architecture() == expected

    def test_unknown_architecture_falls_back(self, monkeypatch):
        monkeypatch.setattr(subprocess, "check_output", MagicMock(return_value=b"mips\n"))
        assert tunnel.detect_unix_architecture() == "unknown"

    def test_command_failure_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(subprocess, "check_output", MagicMock(side_effect=OSError("no uname")))

        with pytest.raises(SystemExit):
            tunnel.detect_unix_architecture()

        assert "Error detecting architecture" in capsys.readouterr().err


class TestDetectArchitecture:
    def test_directly_supported_architecture(self, monkeypatch):
        monkeypatch.setattr(tunnel.platform, "machine", MagicMock(return_value="x86_64"))
        image = tunnel.detect_architecture()
        assert image == tunnel.get_docker_image("x86_64")

    def test_windows_architecture_is_normalized(self, monkeypatch):
        monkeypatch.setattr(tunnel.platform, "machine", MagicMock(return_value="AMD64"))
        image = tunnel.detect_architecture()
        assert image == tunnel.get_docker_image("x86_64")

    def test_falls_back_to_uname_when_unrecognized(self, monkeypatch):
        monkeypatch.setattr(tunnel.platform, "machine", MagicMock(return_value="mystery"))
        monkeypatch.setattr(subprocess, "check_output", MagicMock(return_value=b"arm64\n"))
        image = tunnel.detect_architecture()
        assert image == tunnel.get_docker_image("arm64")


# ---------------------------------------------------------------------------
# SecureTunnel
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_boto_session(monkeypatch):
    """Patch boto3.Session and return the mock iotsecuretunneling client it produces."""
    mock_client = MagicMock()
    mock_session = MagicMock()
    mock_session.client.return_value = mock_client
    mock_session.region_name = "us-west-2"
    monkeypatch.setattr(tunnel.boto3, "Session", MagicMock(return_value=mock_session))
    return mock_session, mock_client


class TestSecureTunnelInit:
    def test_creates_session_and_client(self, mock_boto_session):
        mock_session, mock_client = mock_boto_session

        secure_tunnel = tunnel.SecureTunnel("MyThing", 5555, profile="prof", region="us-west-2")

        tunnel.boto3.Session.assert_called_once_with(profile_name="prof", region_name="us-west-2")
        mock_session.client.assert_called_once_with("iotsecuretunneling")
        assert secure_tunnel.client is mock_client
        assert secure_tunnel.thing_name == "MyThing"
        assert secure_tunnel.port == 5555

    def test_session_error_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(tunnel.boto3, "Session", MagicMock(side_effect=RuntimeError("boom")))

        with pytest.raises(SystemExit):
            tunnel.SecureTunnel("MyThing", 5555)

        assert "boom" in capsys.readouterr().err


def make_secure_tunnel(mock_boto_session, thing_name="MyThing", port=5555):
    return tunnel.SecureTunnel(thing_name, port)


class TestGetExistingTunnelId:
    def test_no_tunnels_returns_none(self, mock_boto_session):
        _, mock_client = mock_boto_session
        mock_client.list_tunnels.return_value = {"tunnelSummaries": []}
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        assert secure_tunnel._get_existing_tunnel_id() is None
        mock_client.list_tunnels.assert_called_once_with(thingName="MyThing")

    def test_single_open_tunnel_returned_directly(self, mock_boto_session):
        _, mock_client = mock_boto_session
        mock_client.list_tunnels.return_value = {
            "tunnelSummaries": [
                {"tunnelId": "t-closed", "status": "CLOSED"},
                {"tunnelId": "t-open", "status": "OPEN"},
            ]
        }
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        assert secure_tunnel._get_existing_tunnel_id() == "t-open"
        mock_client.list_tunnels.assert_called_once_with(thingName="MyThing")

    def test_multiple_open_tunnels_prompts_selection(self, mock_boto_session, monkeypatch):
        _, mock_client = mock_boto_session
        older = {"tunnelId": "t-old", "status": "OPEN", "createdAt": datetime(2024, 1, 1, tzinfo=timezone.utc)}
        newer = {"tunnelId": "t-new", "status": "OPEN", "createdAt": datetime(2025, 1, 1, tzinfo=timezone.utc)}
        mock_client.list_tunnels.return_value = {"tunnelSummaries": [older, newer]}
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        assert secure_tunnel._get_existing_tunnel_id() == "t-new"
        mock_client.list_tunnels.assert_called_once_with(thingName="MyThing")

    def test_list_tunnels_error_exits(self, mock_boto_session, capsys):
        _, mock_client = mock_boto_session
        mock_client.list_tunnels.side_effect = RuntimeError("api down")
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        with pytest.raises(SystemExit):
            secure_tunnel._get_existing_tunnel_id()

        assert "Failed to get existing tunnel ID" in capsys.readouterr().err


class TestSelectTunnel:
    @pytest.fixture
    def open_tunnels(self):
        return [
            {"tunnelId": "t-new", "createdAt": datetime(2025, 1, 1, tzinfo=timezone.utc)},
            {"tunnelId": "t-old", "createdAt": datetime(2024, 1, 1, tzinfo=timezone.utc)},
        ]

    def test_non_interactive_defaults_to_newest(self, mock_boto_session, monkeypatch, open_tunnels):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        assert secure_tunnel._select_tunnel(open_tunnels) == "t-new"

    def test_interactive_empty_input_defaults_to_newest(self, mock_boto_session, monkeypatch, open_tunnels):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt="": "")
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        assert secure_tunnel._select_tunnel(open_tunnels) == "t-new"

    def test_interactive_valid_choice(self, mock_boto_session, monkeypatch, open_tunnels):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt="": "2")
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        assert secure_tunnel._select_tunnel(open_tunnels) == "t-old"

    def test_interactive_invalid_choice_defaults_to_newest(self, mock_boto_session, monkeypatch, open_tunnels, capsys):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt="": "not-a-number")
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        assert secure_tunnel._select_tunnel(open_tunnels) == "t-new"
        assert "Invalid selection" in capsys.readouterr().out

    def test_interactive_out_of_range_defaults_to_newest(self, mock_boto_session, monkeypatch, open_tunnels):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt="": "99")
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        assert secure_tunnel._select_tunnel(open_tunnels) == "t-new"

    def test_interactive_eof_defaults_to_newest(self, mock_boto_session, monkeypatch, open_tunnels):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", _input_raises_eof)
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        assert secure_tunnel._select_tunnel(open_tunnels) == "t-new"


class TestGetAccessTokenClientMode:
    def test_connected_returns_source(self, mock_boto_session):
        _, mock_client = mock_boto_session
        mock_client.describe_tunnel.return_value = {"tunnel": {"destinationConnectionState": {"status": "CONNECTED"}}}
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        assert secure_tunnel._get_access_token_client_mode("t-1") == "SOURCE"
        mock_client.describe_tunnel.assert_called_once_with(tunnelId="t-1")

    def test_not_connected_returns_all(self, mock_boto_session):
        _, mock_client = mock_boto_session
        mock_client.describe_tunnel.return_value = {"tunnel": {"destinationConnectionState": {"status": "DISCONNECTED"}}}
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        assert secure_tunnel._get_access_token_client_mode("t-1") == "ALL"
        mock_client.describe_tunnel.assert_called_once_with(tunnelId="t-1")

    def test_missing_state_returns_all(self, mock_boto_session):
        _, mock_client = mock_boto_session
        mock_client.describe_tunnel.return_value = {"tunnel": {}}
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        assert secure_tunnel._get_access_token_client_mode("t-1") == "ALL"
        mock_client.describe_tunnel.assert_called_once_with(tunnelId="t-1")

    def test_describe_tunnel_error_exits(self, mock_boto_session, capsys):
        _, mock_client = mock_boto_session
        mock_client.describe_tunnel.side_effect = RuntimeError("boom")
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        with pytest.raises(SystemExit):
            secure_tunnel._get_access_token_client_mode("t-1")

        assert "Failed to get access token client mode" in capsys.readouterr().err


class TestRotateAccessTokens:
    def test_all_mode_includes_destination_config(self, mock_boto_session):
        _, mock_client = mock_boto_session
        mock_client.rotate_tunnel_access_token.return_value = {"sourceAccessToken": "tok"}
        secure_tunnel = make_secure_tunnel(mock_boto_session, thing_name="MyThing")

        secure_tunnel._rotate_access_tokens("t-1", "ALL")

        mock_client.rotate_tunnel_access_token.assert_called_once_with(
            tunnelId="t-1",
            clientMode="ALL",
            destinationConfig={"thingName": "MyThing", "services": [tunnel.DEFAULT_SERVICE]},
        )

    def test_source_mode_excludes_destination_config(self, mock_boto_session):
        _, mock_client = mock_boto_session
        mock_client.rotate_tunnel_access_token.return_value = {"sourceAccessToken": "tok"}
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        secure_tunnel._rotate_access_tokens("t-1", "SOURCE")

        mock_client.rotate_tunnel_access_token.assert_called_once_with(tunnelId="t-1", clientMode="SOURCE")

    def test_error_exits(self, mock_boto_session, capsys):
        _, mock_client = mock_boto_session
        mock_client.rotate_tunnel_access_token.side_effect = RuntimeError("boom")
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        with pytest.raises(SystemExit):
            secure_tunnel._rotate_access_tokens("t-1", "ALL")

        assert "Failed to rotate access tokens" in capsys.readouterr().err


class TestOpenNewTunnel:
    def test_success(self, mock_boto_session):
        _, mock_client = mock_boto_session
        mock_client.open_tunnel.return_value = {"tunnelId": "t-new", "sourceAccessToken": "tok"}
        secure_tunnel = make_secure_tunnel(mock_boto_session, thing_name="MyThing")

        response = secure_tunnel._open_new_tunnel()

        assert response == {"tunnelId": "t-new", "sourceAccessToken": "tok"}
        mock_client.open_tunnel.assert_called_once_with(
            destinationConfig={"thingName": "MyThing", "services": [tunnel.DEFAULT_SERVICE]}
        )

    def test_error_exits(self, mock_boto_session, capsys):
        _, mock_client = mock_boto_session
        mock_client.open_tunnel.side_effect = RuntimeError("boom")
        secure_tunnel = make_secure_tunnel(mock_boto_session)

        with pytest.raises(SystemExit):
            secure_tunnel._open_new_tunnel()

        assert "Failed to open new tunnel" in capsys.readouterr().err


class TestGetToken:
    def test_reuses_existing_tunnel(self, mock_boto_session, monkeypatch):
        secure_tunnel = make_secure_tunnel(mock_boto_session)
        monkeypatch.setattr(secure_tunnel, "_get_existing_tunnel_id", MagicMock(return_value="t-existing"))
        monkeypatch.setattr(secure_tunnel, "_get_access_token_client_mode", MagicMock(return_value="SOURCE"))
        monkeypatch.setattr(
            secure_tunnel, "_rotate_access_tokens", MagicMock(return_value={"sourceAccessToken": "tok-1"})
        )

        token, tunnel_id = secure_tunnel.get_token()

        assert (token, tunnel_id) == ("tok-1", "t-existing")

    def test_force_new_skips_lookup(self, mock_boto_session, monkeypatch):
        secure_tunnel = make_secure_tunnel(mock_boto_session)
        lookup = MagicMock(return_value="t-existing")
        monkeypatch.setattr(secure_tunnel, "_get_existing_tunnel_id", lookup)
        monkeypatch.setattr(
            secure_tunnel, "_open_new_tunnel", MagicMock(return_value={"tunnelId": "t-new", "sourceAccessToken": "tok-2"})
        )

        token, tunnel_id = secure_tunnel.get_token(force_new=True)

        lookup.assert_not_called()
        assert (token, tunnel_id) == ("tok-2", "t-new")

    def test_opens_new_tunnel_when_none_exists(self, mock_boto_session, monkeypatch):
        secure_tunnel = make_secure_tunnel(mock_boto_session)
        monkeypatch.setattr(secure_tunnel, "_get_existing_tunnel_id", MagicMock(return_value=None))
        monkeypatch.setattr(
            secure_tunnel, "_open_new_tunnel", MagicMock(return_value={"tunnelId": "t-new", "sourceAccessToken": "tok-3"})
        )

        token, tunnel_id = secure_tunnel.get_token()

        assert (token, tunnel_id) == ("tok-3", "t-new")

    def test_missing_tunnel_id_exits(self, mock_boto_session, monkeypatch, capsys):
        secure_tunnel = make_secure_tunnel(mock_boto_session)
        monkeypatch.setattr(secure_tunnel, "_get_existing_tunnel_id", MagicMock(return_value=None))
        monkeypatch.setattr(secure_tunnel, "_open_new_tunnel", MagicMock(return_value={"sourceAccessToken": "tok"}))

        with pytest.raises(SystemExit):
            secure_tunnel.get_token()

        assert "Failed to retrieve tunnel ID" in capsys.readouterr().err

    @pytest.mark.parametrize("bad_token", [None, "", "null", "NULL"])
    def test_missing_or_null_access_token_exits(self, mock_boto_session, monkeypatch, capsys, bad_token):
        secure_tunnel = make_secure_tunnel(mock_boto_session)
        monkeypatch.setattr(secure_tunnel, "_get_existing_tunnel_id", MagicMock(return_value=None))
        monkeypatch.setattr(
            secure_tunnel, "_open_new_tunnel", MagicMock(return_value={"tunnelId": "t-new", "sourceAccessToken": bad_token})
        )

        with pytest.raises(SystemExit):
            secure_tunnel.get_token()

        assert "Failed to retrieve source access token" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# delete_ssh_fingerprint
# ---------------------------------------------------------------------------


class TestDeleteSshFingerprint:
    def test_success(self, monkeypatch):
        mock_run = MagicMock()
        monkeypatch.setattr(subprocess, "run", mock_run)

        tunnel.delete_ssh_fingerprint("localhost", 5555)

        mock_run.assert_called_once_with(["ssh-keygen", "-R", "[localhost]:5555"], check=True)

    def test_failure_is_reported_not_raised(self, monkeypatch, capsys):
        monkeypatch.setattr(
            subprocess,
            "run",
            MagicMock(side_effect=subprocess.CalledProcessError(1, "ssh-keygen")),
        )

        tunnel.delete_ssh_fingerprint("localhost", 5555)

        assert "Error deleting fingerprint" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# docker_pre_check
# ---------------------------------------------------------------------------


class TestDockerPreCheck:
    def test_returns_client_when_daemon_reachable(self, monkeypatch):
        mock_client = MagicMock()
        monkeypatch.setattr(tunnel.docker, "from_env", MagicMock(return_value=mock_client))

        result = tunnel.docker_pre_check()

        assert result is mock_client
        mock_client.ping.assert_called_once()

    def test_exits_when_daemon_unreachable(self, monkeypatch, capsys):
        monkeypatch.setattr(tunnel.docker, "from_env", MagicMock(side_effect=docker.errors.DockerException("down")))

        with pytest.raises(SystemExit):
            tunnel.docker_pre_check()

        assert "Docker is not running" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# container_name_for / find_container_using_port
# ---------------------------------------------------------------------------


class TestContainerNameFor:
    def test_combines_thing_and_tunnel_id(self):
        assert tunnel.container_name_for("MyThing", "t-123") == "MyThing-t-123"


class TestFindContainerUsingPort:
    def test_returns_match(self):
        mock_client = MagicMock()
        found = MagicMock()
        mock_client.containers.list.return_value = [found]

        result = tunnel.find_container_using_port(mock_client, 5555)

        assert result is found
        mock_client.containers.list.assert_called_once_with(filters={"publish": "5555/tcp"})

    def test_returns_none_when_no_match(self):
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []

        assert tunnel.find_container_using_port(mock_client, 5555) is None

    def test_exits_on_docker_error(self, capsys):
        mock_client = MagicMock()
        mock_client.containers.list.side_effect = docker.errors.DockerException("boom")

        with pytest.raises(SystemExit):
            tunnel.find_container_using_port(mock_client, 5555)

        assert "Failed to list Docker containers" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# prompt_yes_no
# ---------------------------------------------------------------------------


class TestPromptYesNo:
    def test_non_interactive_defaults_no(self, monkeypatch):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        assert tunnel.prompt_yes_no("Continue? ") is False

    @pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES"])
    def test_interactive_yes_variants(self, monkeypatch, answer):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt="": answer)
        assert tunnel.prompt_yes_no("Continue? ") is True

    @pytest.mark.parametrize("answer", ["n", "no", "", "maybe"])
    def test_interactive_no_variants(self, monkeypatch, answer):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt="": answer)
        assert tunnel.prompt_yes_no("Continue? ") is False

    def test_eof_defaults_no(self, monkeypatch):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", _input_raises_eof)
        assert tunnel.prompt_yes_no("Continue? ") is False


# ---------------------------------------------------------------------------
# resolve_container_conflict
# ---------------------------------------------------------------------------


class TestResolveContainerConflict:
    def test_accepting_stops_container(self, monkeypatch):
        monkeypatch.setattr(tunnel, "prompt_yes_no", MagicMock(return_value=True))
        monkeypatch.setattr(tunnel.time, "sleep", MagicMock())
        container = MagicMock()
        container.name = "c1"

        tunnel.resolve_container_conflict(container, "conflict description")

        container.stop.assert_called_once()
        container.wait.assert_called_once()

    def test_declining_exits_without_stopping(self, monkeypatch):
        monkeypatch.setattr(tunnel, "prompt_yes_no", MagicMock(return_value=False))
        container = MagicMock()
        container.name = "c1"

        with pytest.raises(SystemExit):
            tunnel.resolve_container_conflict(container, "conflict description")

        container.stop.assert_not_called()

    def test_container_already_gone_is_handled(self, monkeypatch):
        monkeypatch.setattr(tunnel, "prompt_yes_no", MagicMock(return_value=True))
        monkeypatch.setattr(tunnel.time, "sleep", MagicMock())
        container = MagicMock()
        container.name = "c1"
        container.stop.side_effect = docker.errors.NotFound("gone")

        tunnel.resolve_container_conflict(container, "conflict description")  # should not raise

    def test_docker_error_while_stopping_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(tunnel, "prompt_yes_no", MagicMock(return_value=True))
        container = MagicMock()
        container.name = "c1"
        container.stop.side_effect = docker.errors.DockerException("boom")

        with pytest.raises(SystemExit):
            tunnel.resolve_container_conflict(container, "conflict description")

        assert "Error stopping container" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# run_docker_container
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_docker_client(monkeypatch):
    mock_client = MagicMock()
    monkeypatch.setattr(tunnel, "docker_pre_check", MagicMock(return_value=mock_client))
    monkeypatch.setattr(tunnel, "find_container_using_port", MagicMock(return_value=None))
    mock_client.containers.get.side_effect = docker.errors.NotFound("no container")
    return mock_client


class TestRunDockerContainer:
    def test_starts_container_when_no_conflicts(self, mock_docker_client):
        tunnel.run_docker_container("us-west-2", "some-image", "MyThing", "tok", 5555, "t-1")

        mock_docker_client.containers.run.assert_called_once()
        _, kwargs = mock_docker_client.containers.run.call_args
        assert kwargs["image"] == "some-image"
        assert kwargs["name"] == "MyThing-t-1"
        assert kwargs["environment"] == {"AWSIOT_TUNNEL_ACCESS_TOKEN": "tok"}
        assert kwargs["ports"] == {"5555/tcp": 5555}
        assert kwargs["labels"] == {tunnel.LABEL_THING_NAME: "MyThing", tunnel.LABEL_TUNNEL_ID: "t-1"}
        assert kwargs["detach"] is True
        assert kwargs["remove"] is True
        assert "--region us-west-2" in kwargs["command"]
        assert "--destination-client-type V1" in kwargs["command"]

    def test_client_type_defaults_to_v1(self, mock_docker_client):
        tunnel.run_docker_container("us-west-2", "some-image", "MyThing", "tok", 5555, "t-1")

        _, kwargs = mock_docker_client.containers.run.call_args
        assert "--destination-client-type V1" in kwargs["command"]

    def test_use_v2_switches_client_type_to_v2(self, mock_docker_client):
        tunnel.run_docker_container("us-west-2", "some-image", "MyThing", "tok", 5555, "t-1", use_v2=True)

        _, kwargs = mock_docker_client.containers.run.call_args
        assert "--destination-client-type V2" in kwargs["command"]

    def test_port_conflict_prompts_resolution(self, mock_docker_client, monkeypatch):
        conflicting_container = MagicMock()
        conflicting_container.labels = {tunnel.LABEL_THING_NAME: "OtherThing", tunnel.LABEL_TUNNEL_ID: "t-other"}
        monkeypatch.setattr(tunnel, "find_container_using_port", MagicMock(return_value=conflicting_container))
        resolve_mock = MagicMock()
        monkeypatch.setattr(tunnel, "resolve_container_conflict", resolve_mock)

        tunnel.run_docker_container("us-west-2", "some-image", "MyThing", "tok", 5555, "t-1")

        resolve_mock.assert_called_once()
        assert resolve_mock.call_args[0][0] is conflicting_container
        mock_docker_client.containers.run.assert_called_once()

    def test_running_container_for_same_tunnel_prompts_resolution(self, mock_docker_client, monkeypatch):
        existing = MagicMock()
        existing.status = "running"
        mock_docker_client.containers.get.side_effect = None
        mock_docker_client.containers.get.return_value = existing
        resolve_mock = MagicMock()
        monkeypatch.setattr(tunnel, "resolve_container_conflict", resolve_mock)

        tunnel.run_docker_container("us-west-2", "some-image", "MyThing", "tok", 5555, "t-1")

        resolve_mock.assert_called_once()
        assert resolve_mock.call_args[0][0] is existing
        mock_docker_client.containers.run.assert_called_once()

    def test_stale_stopped_container_is_removed_silently(self, mock_docker_client, monkeypatch):
        stale = MagicMock()
        stale.status = "exited"
        mock_docker_client.containers.get.side_effect = None
        mock_docker_client.containers.get.return_value = stale
        resolve_mock = MagicMock()
        monkeypatch.setattr(tunnel, "resolve_container_conflict", resolve_mock)

        tunnel.run_docker_container("us-west-2", "some-image", "MyThing", "tok", 5555, "t-1")

        resolve_mock.assert_not_called()
        stale.remove.assert_called_once_with(force=True)
        mock_docker_client.containers.run.assert_called_once()

    def test_stale_container_already_removed_is_ignored(self, mock_docker_client):
        stale = MagicMock()
        stale.status = "exited"
        stale.remove.side_effect = docker.errors.NotFound("already gone")
        mock_docker_client.containers.get.side_effect = None
        mock_docker_client.containers.get.return_value = stale

        tunnel.run_docker_container("us-west-2", "some-image", "MyThing", "tok", 5555, "t-1")  # should not raise

        mock_docker_client.containers.run.assert_called_once()

    def test_inspect_failure_exits(self, mock_docker_client, capsys):
        mock_docker_client.containers.get.side_effect = docker.errors.DockerException("inspect boom")

        with pytest.raises(SystemExit):
            tunnel.run_docker_container("us-west-2", "some-image", "MyThing", "tok", 5555, "t-1")

        assert "Failed to inspect existing container" in capsys.readouterr().err

    def test_stale_container_remove_failure_is_a_warning_not_fatal(self, mock_docker_client, capsys):
        stale = MagicMock()
        stale.status = "exited"
        stale.remove.side_effect = docker.errors.DockerException("remove boom")
        mock_docker_client.containers.get.side_effect = None
        mock_docker_client.containers.get.return_value = stale

        tunnel.run_docker_container("us-west-2", "some-image", "MyThing", "tok", 5555, "t-1")  # should not raise

        assert "Warning: Failed to remove stale container" in capsys.readouterr().err
        mock_docker_client.containers.run.assert_called_once()

    def test_run_failure_exits(self, mock_docker_client, capsys):
        mock_docker_client.containers.run.side_effect = docker.errors.DockerException("boom")

        with pytest.raises(SystemExit):
            tunnel.run_docker_container("us-west-2", "some-image", "MyThing", "tok", 5555, "t-1")

        assert "Failed to start Docker container" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    @pytest.fixture
    def wired_main(self, monkeypatch):
        args = MagicMock(
            thing_name="MyThing", profile=None, region=None, port=5555, remove_fingerprint=False, new_tunnel=False, v2=False
        )
        monkeypatch.setattr(tunnel, "parse_arguments", MagicMock(return_value=args))
        monkeypatch.setattr(tunnel, "docker_pre_check", MagicMock())
        monkeypatch.setattr(tunnel, "detect_architecture", MagicMock(return_value="some-image"))

        mock_secure_tunnel = MagicMock()
        mock_secure_tunnel.get_token.return_value = ("tok", "t-1")
        mock_secure_tunnel.session.region_name = "us-west-2"
        secure_tunnel_class_mock = MagicMock(return_value=mock_secure_tunnel)
        monkeypatch.setattr(tunnel, "SecureTunnel", secure_tunnel_class_mock)

        run_container_mock = MagicMock()
        monkeypatch.setattr(tunnel, "run_docker_container", run_container_mock)

        delete_fingerprint_mock = MagicMock()
        monkeypatch.setattr(tunnel, "delete_ssh_fingerprint", delete_fingerprint_mock)

        return args, secure_tunnel_class_mock, mock_secure_tunnel, run_container_mock, delete_fingerprint_mock

    def test_happy_path_starts_tunnel_container(self, wired_main):
        args, secure_tunnel_class_mock, mock_secure_tunnel, run_container_mock, delete_fingerprint_mock = wired_main

        tunnel.main()

        secure_tunnel_class_mock.assert_called_once_with(args.thing_name, args.port, args.profile, args.region)
        mock_secure_tunnel.get_token.assert_called_once_with(force_new=False)
        run_container_mock.assert_called_once_with("us-west-2", "some-image", "MyThing", "tok", 5555, "t-1", False)
        delete_fingerprint_mock.assert_not_called()

    def test_remove_fingerprint_flag_deletes_fingerprint(self, wired_main):
        args, secure_tunnel_class_mock, mock_secure_tunnel, run_container_mock, delete_fingerprint_mock = wired_main
        args.remove_fingerprint = True

        tunnel.main()

        delete_fingerprint_mock.assert_called_once_with("localhost", 5555)

    def test_new_tunnel_flag_forces_new_tunnel(self, wired_main):
        args, secure_tunnel_class_mock, mock_secure_tunnel, run_container_mock, delete_fingerprint_mock = wired_main
        args.new_tunnel = True

        tunnel.main()

        mock_secure_tunnel.get_token.assert_called_once_with(force_new=True)

    def test_v2_flag_selects_v2_client_type(self, wired_main):
        args, secure_tunnel_class_mock, mock_secure_tunnel, run_container_mock, delete_fingerprint_mock = wired_main
        args.v2 = True

        tunnel.main()

        run_container_mock.assert_called_once_with("us-west-2", "some-image", "MyThing", "tok", 5555, "t-1", True)
