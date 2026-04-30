"""CLI for the standalone HYMER token tool."""

from __future__ import annotations

import argparse
import asyncio
import configparser
from dataclasses import asdict
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import sys
from typing import Any

import aiohttp

from .api import HymerAuthError, HymerCloudClient, HymerTokenToolError, VehicleRecord
from .ble import BleSupportError, DiscoveredBleDevice, probe_device, scan_devices
from .scu import (
    DEFAULT_WAKE_DELAY,
    ScuBleSession,
    ScuBleSessionError,
    default_mobile_device_name,
)
from .tls import TlsSupportError, run_tls_loopback_self_test
from .tokens import (
    coerce_remote_access_refresh_token,
    decode_jwt_without_verification,
    find_remote_access_refresh_token,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hymer-token-tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--brand", default="hymer")
    common.add_argument("--ini-file", type=Path)
    common.add_argument("--username")
    common.add_argument("--password")
    common.add_argument("--json", action="store_true", dest="json_output")

    wizard = subparsers.add_parser("wizard", parents=[common])
    wizard.add_argument("--vehicle-index", type=int)
    wizard.add_argument("--activation-token")
    wizard.add_argument("--session-file", type=Path)
    wizard.add_argument("--manual-vin")
    wizard.add_argument("--manual-model-name")
    wizard.add_argument("--manual-scu-id")
    wizard.add_argument("--manual-vehicle-urn")
    wizard.add_argument("--manual-scu-urn")

    login = subparsers.add_parser("login", parents=[common])
    login.add_argument("--vehicle-index", type=int)

    inspect_activation = subparsers.add_parser(
        "inspect-activation",
        parents=[common],
    )
    inspect_activation.add_argument("--activation-token", required=True)

    validate_remote = subparsers.add_parser(
        "validate-remote-refresh",
        parents=[common],
    )
    validate_remote.add_argument("--vehicle-urn", required=True)
    validate_remote.add_argument("--remote-refresh-token", required=True)

    extract_remote = subparsers.add_parser("extract-remote-refresh")
    extract_remote.add_argument("--input-file", type=Path)
    extract_remote.add_argument(
        "--token-file",
        type=Path,
        default=Path("remote-access-refresh-token.txt"),
    )
    extract_remote.add_argument("--print-token", action="store_true")
    extract_remote.add_argument("--json", action="store_true", dest="json_output")

    ble_scan = subparsers.add_parser("ble-scan")
    ble_scan.add_argument("--timeout", type=float, default=8.0)
    ble_scan.add_argument("--name-contains", default="")
    ble_scan.add_argument("--json", action="store_true", dest="json_output")

    ble_probe = subparsers.add_parser("ble-probe")
    ble_probe.add_argument("--identifier", required=True)
    ble_probe.add_argument("--timeout", type=float, default=10.0)
    ble_probe.add_argument("--json", action="store_true", dest="json_output")

    tls_self_test = subparsers.add_parser("tls-self-test")
    tls_self_test.add_argument("--json", action="store_true", dest="json_output")

    scu_tls_probe = subparsers.add_parser("scu-tls-probe")
    scu_tls_probe.add_argument("--identifier", required=True)
    scu_tls_probe.add_argument("--timeout", type=float, default=10.0)
    scu_tls_probe.add_argument("--handshake-timeout", type=float, default=20.0)
    scu_tls_probe.add_argument("--write-chunk-size", type=int)
    scu_tls_probe.add_argument("--bond", action="store_true")
    scu_tls_probe.add_argument("--probe-bonding-state", action="store_true")
    scu_tls_probe.add_argument("--wake-up", action="store_true")
    scu_tls_probe.add_argument("--wake-delay", type=float, default=DEFAULT_WAKE_DELAY)
    scu_tls_probe.add_argument("--json", action="store_true", dest="json_output")

    scu_pair_mobile = subparsers.add_parser("scu-pair-mobile")
    scu_pair_mobile.add_argument("--identifier", required=True)
    scu_pair_mobile.add_argument("--activation-token", required=True)
    scu_pair_mobile.add_argument("--confirmation-token", required=True)
    scu_pair_mobile.add_argument("--mobile-device-name")
    scu_pair_mobile.add_argument("--timeout", type=float, default=10.0)
    scu_pair_mobile.add_argument("--pair-timeout", type=float, default=30.0)
    scu_pair_mobile.add_argument("--write-chunk-size", type=int)
    scu_pair_mobile.add_argument("--bond", action="store_true")
    scu_pair_mobile.add_argument("--probe-bonding-state", action="store_true")
    scu_pair_mobile.add_argument("--wake-up", action="store_true")
    scu_pair_mobile.add_argument("--wake-delay", type=float, default=DEFAULT_WAKE_DELAY)
    scu_pair_mobile.add_argument("--skip-confirmation", action="store_true")
    scu_pair_mobile.add_argument("--json", action="store_true", dest="json_output")

    mint_remote_refresh = subparsers.add_parser("mint-remote-refresh", parents=[common])
    mint_remote_refresh.add_argument("--activation-token")
    mint_remote_refresh.add_argument("--identifier")
    mint_remote_refresh.add_argument("--mobile-device-name")
    mint_remote_refresh.add_argument("--scan-timeout", type=float, default=8.0)
    mint_remote_refresh.add_argument("--name-contains", default="")
    mint_remote_refresh.add_argument("--timeout", type=float, default=10.0)
    mint_remote_refresh.add_argument("--pair-timeout", type=float, default=30.0)
    mint_remote_refresh.add_argument("--wake-delay", type=float, default=DEFAULT_WAKE_DELAY)
    mint_remote_refresh.add_argument(
        "--token-file",
        type=Path,
        default=Path("remote-access-refresh-token.txt"),
    )
    mint_remote_refresh.add_argument("--session-file", type=Path)

    return parser


def _prompt_if_missing(value: str | None, prompt: str, *, secret: bool = False) -> str:
    if value:
        return value
    return getpass.getpass(prompt) if secret else input(prompt)


def _optional_prompt(value: str | None, prompt: str) -> str:
    if value is not None:
        return value
    return input(prompt)


def _json_dump(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def _load_ini_values(args: argparse.Namespace) -> dict[str, str]:
    cached = getattr(args, "_ini_values", None)
    if cached is not None:
        return cached
    ini_file = getattr(args, "ini_file", None)
    if ini_file is None:
        values: dict[str, str] = {}
        setattr(args, "_ini_values", values)
        return values
    if not ini_file.exists():
        raise HymerTokenToolError(f"INI file does not exist: {ini_file}")
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ini_file, encoding="utf-8")
    values = {key.replace("-", "_"): value for key, value in parser.defaults().items()}
    for section_name in ("hymer_token_tool", "hymer-token-tool", "hymer", "auth"):
        if parser.has_section(section_name):
            values.update(
                {
                    key.replace("-", "_"): value
                    for key, value in parser.items(section_name)
                }
            )
    setattr(args, "_ini_values", values)
    return values


def _value_from_cli_or_ini(args: argparse.Namespace, key: str) -> str | None:
    value = getattr(args, key, None)
    if value:
        return value
    ini_values = _load_ini_values(args)
    configured = ini_values.get(key)
    if not configured:
        return None
    text = configured.strip()
    return text or None


def _decode_jwt_without_verification(token: str) -> dict[str, Any]:
    return decode_jwt_without_verification(token)


def _format_vehicle(vehicle: VehicleRecord, index: int) -> str:
    bits = [f"[{index}] {vehicle.title}"]
    if vehicle.vehicle_urn:
        bits.append(f"vehicleUrn={vehicle.vehicle_urn}")
    if vehicle.scu_urn:
        bits.append(f"scuUrn={vehicle.scu_urn}")
    if vehicle.vin:
        bits.append(f"VIN={vehicle.vin}")
    return " | ".join(bits)


def _choose_vehicle(
    vehicles: list[VehicleRecord],
    *,
    explicit_index: int | None,
    interactive: bool,
) -> VehicleRecord | None:
    if not vehicles:
        return None
    if explicit_index is not None:
        if explicit_index < 0 or explicit_index >= len(vehicles):
            raise HymerTokenToolError(
                f"Vehicle index {explicit_index} is out of range for {len(vehicles)} vehicles"
            )
        return vehicles[explicit_index]
    if len(vehicles) == 1 or not interactive:
        return vehicles[0]
    print("Select vehicle:")
    for index, vehicle in enumerate(vehicles):
        print(_format_vehicle(vehicle, index))
    raw = input("Vehicle index: ").strip()
    if not raw:
        return vehicles[0]
    try:
        index = int(raw)
    except ValueError as err:
        raise HymerTokenToolError("Vehicle index must be an integer") from err
    if index < 0 or index >= len(vehicles):
        raise HymerTokenToolError(
            f"Vehicle index {index} is out of range for {len(vehicles)} vehicles"
        )
    return vehicles[index]


def _write_session_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dump(payload) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(path, 0o600)


def _write_secret_text_file(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(path, 0o600)


def _format_ble_device(device: DiscoveredBleDevice, index: int) -> str:
    bits = [f"[{index}] {device.name or '(unknown)'}", f"id={device.identifier}"]
    if device.rssi is not None:
        bits.append(f"rssi={device.rssi}")
    if device.service_uuids:
        bits.append(f"services={','.join(device.service_uuids)}")
    return " | ".join(bits)


def _choose_ble_device(
    devices: list[DiscoveredBleDevice],
    *,
    explicit_identifier: str | None,
    interactive: bool,
) -> DiscoveredBleDevice:
    if explicit_identifier:
        for device in devices:
            if device.identifier == explicit_identifier or device.address == explicit_identifier:
                return device
        return DiscoveredBleDevice(
            identifier=explicit_identifier,
            name="",
            address=explicit_identifier,
            rssi=None,
            manufacturer_data={},
            service_uuids=[],
        )
    if not devices:
        raise HymerTokenToolError("No BLE devices found")
    if len(devices) == 1 or not interactive:
        return devices[0]
    print("Select BLE device:")
    for index, device in enumerate(devices):
        print(_format_ble_device(device, index))
    raw = input("BLE device index: ").strip()
    if not raw:
        return devices[0]
    try:
        index = int(raw)
    except ValueError as err:
        raise HymerTokenToolError("BLE device index must be an integer") from err
    if index < 0 or index >= len(devices):
        raise HymerTokenToolError(
            f"BLE device index {index} is out of range for {len(devices)} devices"
        )
    return devices[index]


async def _with_client(
    args: argparse.Namespace,
    *,
    require_auth: bool = True,
) -> tuple[HymerCloudClient, str, str]:
    username = _prompt_if_missing(
        _value_from_cli_or_ini(args, "username"),
        "Username/email: ",
    )
    password = _prompt_if_missing(
        _value_from_cli_or_ini(args, "password"),
        "Password: ",
        secret=True,
    )
    session = aiohttp.ClientSession()
    client = HymerCloudClient(session, brand=args.brand)
    try:
        if require_auth:
            await client.authenticate(username, password)
        return client, username, password
    except Exception:
        await session.close()
        raise


async def command_login(args: argparse.Namespace) -> int:
    client, _, _ = await _with_client(args)
    try:
        account = await client.get_account()
        vehicles = await client.discover_vehicles()
        selected = _choose_vehicle(
            vehicles,
            explicit_index=args.vehicle_index,
            interactive=False,
        )
        payload = {
            "account": account,
            "vehicles": [vehicle.to_dict() for vehicle in vehicles],
            "selected_vehicle": selected.to_dict() if selected else None,
        }
        if args.json_output:
            print(_json_dump(payload))
        else:
            print(f"Account: {account.get('email') or account.get('username') or '(unknown)'}")
            if not vehicles:
                print("Vehicles: none")
            for index, vehicle in enumerate(vehicles):
                print(_format_vehicle(vehicle, index))
        return 0
    finally:
        await client._session.close()


async def command_wizard(args: argparse.Namespace) -> int:
    client, username, _ = await _with_client(args)
    try:
        account = await client.get_account()
        vehicles = await client.discover_vehicles()
        selected = _choose_vehicle(
            vehicles,
            explicit_index=args.vehicle_index,
            interactive=not args.json_output,
        )
        confirmation_token = await client.get_confirmation_token_value()
        activation_lookup: dict[str, Any] | None = None
        if args.activation_token:
            activation_lookup = await client.get_vehicle_by_token(args.activation_token)

        selected_model_name = selected.model if selected else ""
        selected_vin = selected.vin if selected else ""
        selected_vehicle_urn = selected.vehicle_urn if selected else ""
        selected_scu_urn = selected.scu_urn if selected else ""
        derived_scu_id = (
            selected_scu_urn.rsplit(":", maxsplit=1)[-1] if selected_scu_urn else ""
        )

        manual_inputs = {
            "vin": _optional_prompt(
                args.manual_vin if args.json_output else args.manual_vin,
                f"VIN [{selected_vin}]: ",
            ).strip()
            if not args.json_output
            else (args.manual_vin or "").strip(),
            "model_name": _optional_prompt(
                args.manual_model_name if args.json_output else args.manual_model_name,
                f"Model name [{selected_model_name}]: ",
            ).strip()
            if not args.json_output
            else (args.manual_model_name or "").strip(),
            "scu_id": _optional_prompt(
                args.manual_scu_id if args.json_output else args.manual_scu_id,
                f"SCU ID [{derived_scu_id}]: ",
            ).strip()
            if not args.json_output
            else (args.manual_scu_id or "").strip(),
            "vehicle_urn": _optional_prompt(
                args.manual_vehicle_urn if args.json_output else args.manual_vehicle_urn,
                f"Vehicle URN [{selected_vehicle_urn}]: ",
            ).strip()
            if not args.json_output
            else (args.manual_vehicle_urn or "").strip(),
            "scu_urn": _optional_prompt(
                args.manual_scu_urn if args.json_output else args.manual_scu_urn,
                f"SCU URN [{selected_scu_urn}]: ",
            ).strip()
            if not args.json_output
            else (args.manual_scu_urn or "").strip(),
        }
        if not manual_inputs["vin"] and selected_vin:
            manual_inputs["vin"] = selected_vin
        if not manual_inputs["model_name"] and selected_model_name:
            manual_inputs["model_name"] = selected_model_name
        if not manual_inputs["vehicle_urn"] and selected_vehicle_urn:
            manual_inputs["vehicle_urn"] = selected_vehicle_urn
        if not manual_inputs["scu_urn"] and selected_scu_urn:
            manual_inputs["scu_urn"] = selected_scu_urn
        if not manual_inputs["scu_id"] and derived_scu_id:
            manual_inputs["scu_id"] = derived_scu_id
        manual_inputs = {key: value for key, value in manual_inputs.items() if value}

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "brand": args.brand,
            "username": username,
            "account": account,
            "vehicles": [vehicle.to_dict() for vehicle in vehicles],
            "selected_vehicle": selected.to_dict() if selected else None,
            "confirmation_token": confirmation_token,
            "activation_lookup": activation_lookup,
            "manual_pairing_inputs": manual_inputs,
            "oauth": {
                "access_token": client.access_token,
                "refresh_token": client.refresh_token,
            },
            "pairing_status": {
                "cloud_preflight_complete": True,
                "ble_pairing_transport_complete": False,
                "ble_pairing_transport_experimental": True,
                "note": (
                    "OAuth, vehicle discovery, confirmation token, and activation-token "
                    "lookup are complete. The standalone tool now has an early-alpha SCU "
                    "BLE/TLS transport and pairing path, but it has not yet been verified "
                    "end-to-end against a real vehicle in this environment and should not "
                    "yet be used for live token minting."
                ),
            },
        }
        if args.session_file:
            _write_session_file(args.session_file, payload)
        if args.json_output:
            print(_json_dump(payload))
        else:
            print(f"Logged in as: {account.get('email') or username}")
            print(f"Vehicles discovered: {len(vehicles)}")
            if selected:
                print(f"Selected vehicle: {selected.title}")
                if selected.vehicle_urn:
                    print(f"Vehicle URN: {selected.vehicle_urn}")
                if selected.scu_urn:
                    print(f"SCU URN: {selected.scu_urn}")
            print(f"Confirmation token: {confirmation_token}")
            if activation_lookup is not None:
                print("Activation-token lookup:")
                print(_json_dump(activation_lookup))
            if manual_inputs:
                print("Manual pairing inputs:")
                print(_json_dump(manual_inputs))
            if args.session_file:
                print(f"Session written to: {args.session_file}")
            print(
                "Pairing status: cloud side complete; live BLE pairing path is "
                "early alpha, not yet hardware-verified, and not ready for live use."
            )
        return 0
    finally:
        await client._session.close()


async def command_inspect_activation(args: argparse.Namespace) -> int:
    client, _, _ = await _with_client(args)
    try:
        result = await client.get_vehicle_by_token(args.activation_token)
        if args.json_output:
            print(_json_dump(result))
        else:
            print(_json_dump(result))
        return 0
    finally:
        await client._session.close()


async def command_validate_remote_refresh(args: argparse.Namespace) -> int:
    client, _, _ = await _with_client(args)
    try:
        remote_refresh_token = coerce_remote_access_refresh_token(
            args.remote_refresh_token
        )
        access_token = await client.get_remote_access_token(
            args.vehicle_urn,
            remote_refresh_token,
        )
        decoded = _decode_jwt_without_verification(access_token)
        payload = {
            "vehicle_urn": args.vehicle_urn,
            "valid": True,
            "remote_access_token": access_token,
            "decoded": decoded,
        }
        if args.json_output:
            print(_json_dump(payload))
        else:
            print(f"Validation succeeded for {args.vehicle_urn}")
            token_payload = decoded.get("payload", {})
            for key in ("sub", "aud", "ett", "exp", "nbf", "iat"):
                if key in token_payload:
                    print(f"{key}: {token_payload[key]}")
        return 0
    finally:
        await client._session.close()


async def command_extract_remote_refresh(args: argparse.Namespace) -> int:
    if args.input_file:
        text = args.input_file.read_text(encoding="utf-8", errors="replace")
        source = str(args.input_file)
    else:
        text = sys.stdin.read()
        source = "stdin"
    token = find_remote_access_refresh_token(text)
    if token is None:
        raise HymerTokenToolError(
            "No EHG remote-access refresh token was found in the input"
        )
    decoded = decode_jwt_without_verification(token)
    _write_secret_text_file(args.token_file, token)
    payload = {
        "source": source,
        "found": True,
        "token_file": str(args.token_file),
        "decoded": decoded,
    }
    if args.print_token:
        payload["remote_refresh_token"] = token
    if args.json_output:
        print(_json_dump(payload))
    else:
        print(f"Remote refresh token written to: {args.token_file}")
        token_payload = decoded.get("payload", {})
        for key in ("urn", "sub", "ett", "client_id"):
            if key in token_payload:
                print(f"{key}: {token_payload[key]}")
        if args.print_token:
            print(token)
        else:
            print("Token value suppressed. Use --print-token only if you need stdout output.")
    return 0


async def command_ble_scan(args: argparse.Namespace) -> int:
    devices = await scan_devices(timeout=args.timeout, name_contains=args.name_contains)
    payload = {"devices": [device.to_dict() for device in devices]}
    if args.json_output:
        print(_json_dump(payload))
    else:
        if not devices:
            print("No BLE devices found.")
            return 0
        for device in devices:
            print(
                f"{device.identifier} | name={device.name or '(unknown)'} "
                f"| rssi={device.rssi} | services={','.join(device.service_uuids)}"
            )
    return 0


async def command_ble_probe(args: argparse.Namespace) -> int:
    services = await probe_device(args.identifier, timeout=args.timeout)
    payload = {"identifier": args.identifier, "services": [item.to_dict() for item in services]}
    if args.json_output:
        print(_json_dump(payload))
    else:
        print(f"Services for {args.identifier}:")
        for service in services:
            print(f"- {service.uuid}")
            for characteristic in service.characteristics:
                print(
                    f"  - {characteristic.uuid} "
                    f"({', '.join(characteristic.properties)})"
                )
    return 0


async def command_tls_self_test(args: argparse.Namespace) -> int:
    results = run_tls_loopback_self_test()
    payload = {
        "python_ssl": __import__("ssl").OPENSSL_VERSION,
        "results": [item.to_dict() for item in results],
    }
    if args.json_output:
        print(_json_dump(payload))
    else:
        print(f"Python SSL: {payload['python_ssl']}")
        for result in results:
            print(
                f"{result.requested_tls_version}: negotiated={result.negotiated_tls_version} "
                f"cipher={result.cipher_suite} "
                f"rx={result.client_received_hex}"
            )
    return 0


async def command_scu_tls_probe(args: argparse.Namespace) -> int:
    session = ScuBleSession(
        args.identifier,
        connect_timeout=args.timeout,
        write_chunk_size=args.write_chunk_size,
    )
    try:
        await session.connect(bond=args.bond)
        result = await session.probe_tls(
            wake_up=args.wake_up,
            wake_delay=args.wake_delay,
            probe_bonding_state=args.probe_bonding_state,
            timeout=args.handshake_timeout,
        )
    finally:
        await session.disconnect()
    payload = result.to_dict()
    if args.json_output:
        print(_json_dump(payload))
    else:
        print(f"SCU device: {payload['device_name']} ({payload['identifier']})")
        print(
            f"Bonding: requested={payload['bond_requested']} "
            f"status={payload['bond_status']}"
        )
        print(
            f"TLS: {payload['negotiated_tls_version']} "
            f"{payload['cipher_suite']} ({payload['cipher_bits']} bits)"
        )
        print(
            f"Writes: response={payload['write_with_response']} "
            f"chunk_size={payload['write_chunk_size']} mtu={payload['mtu_size']}"
        )
        if payload["power_state_before_hex"] is not None:
            print(f"Power state before: {payload['power_state_before_hex']}")
        if payload["power_state_after_wake_hex"] is not None:
            print(f"Power state after wake: {payload['power_state_after_wake_hex']}")
        if payload["bonding_state_probe"] is not None:
            print(
                "Bonding-state: "
                f"value={payload['bonding_state_probe']['state_value']} "
                f"response={payload['bonding_state_probe']['response_hex']}"
            )
    return 0


async def command_scu_pair_mobile(args: argparse.Namespace) -> int:
    session = ScuBleSession(
        args.identifier,
        connect_timeout=args.timeout,
        write_chunk_size=args.write_chunk_size,
    )
    try:
        await session.connect(bond=args.bond)
        result = await session.pair_mobile_device(
            activation_token=args.activation_token,
            confirmation_token=args.confirmation_token,
            mobile_device_name=args.mobile_device_name or default_mobile_device_name(),
            wake_up=args.wake_up,
            wake_delay=args.wake_delay,
            probe_bonding_state=args.probe_bonding_state,
            timeout=args.pair_timeout,
            send_confirmation=not args.skip_confirmation,
        )
    finally:
        await session.disconnect()
    payload = result.to_dict()
    if args.json_output:
        print(_json_dump(payload))
    else:
        print(f"SCU device: {payload['device_name']} ({payload['identifier']})")
        print(
            f"Bonding: requested={payload['bond_requested']} "
            f"status={payload['bond_status']}"
        )
        print(
            f"TLS: {payload['negotiated_tls_version']} "
            f"{payload['cipher_suite']} ({payload['cipher_bits']} bits)"
        )
        print(f"Mobile device name: {payload['mobile_device_name']}")
        if payload["bonding_state_probe"] is not None:
            print(
                "Bonding-state: "
                f"value={payload['bonding_state_probe']['state_value']} "
                f"response={payload['bonding_state_probe']['response_hex']}"
            )
        print(
            f"Confirmation sent: {payload['confirmation_sent']} "
            f"| raw confirmationRequired: "
            f"{payload['pair_mobile_response']['confirmation_required']}"
        )
        print(
            "Remote refresh token: "
            f"{payload['pair_mobile_response']['remote_access_refresh_token']}"
        )
    return 0


async def command_mint_remote_refresh(args: argparse.Namespace) -> int:
    activation_token = _prompt_if_missing(
        args.activation_token,
        "Activation token / QR value: ",
    ).strip()
    if not activation_token:
        raise HymerTokenToolError("Activation token is required")

    client, username, _ = await _with_client(args)
    try:
        account = await client.get_account()
        confirmation_token = await client.get_confirmation_token_value()
        activation_lookup = await client.get_vehicle_by_token(activation_token)

        devices = (
            await scan_devices(timeout=args.scan_timeout, name_contains=args.name_contains)
            if not args.identifier
            else []
        )
        selected_device = _choose_ble_device(
            devices,
            explicit_identifier=args.identifier,
            interactive=not args.json_output,
        )
        mobile_device_name = args.mobile_device_name or default_mobile_device_name()

        if not args.json_output:
            print(f"Account: {account.get('email') or username}")
            print(f"Using BLE device: {_format_ble_device(selected_device, 0)}")
            print(
                "The tool will now request BLE bonding with the SCU. Accept any OS "
                "pairing dialog that appears."
            )
            input("Press Enter when you are ready to start pairing: ")

        session = ScuBleSession(
            selected_device.identifier,
            connect_timeout=args.timeout,
        )
        try:
            await session.connect(bond=True)
            result = await session.pair_mobile_device(
                activation_token=activation_token,
                confirmation_token=confirmation_token,
                mobile_device_name=mobile_device_name,
                wake_up=True,
                wake_delay=args.wake_delay,
                probe_bonding_state=True,
                timeout=args.pair_timeout,
                send_confirmation=True,
            )
        finally:
            await session.disconnect()

        payload = result.to_dict()
        remote_refresh_token = payload["pair_mobile_response"]["remote_access_refresh_token"]
        if not remote_refresh_token:
            raise HymerTokenToolError(
                "SCU pairing did not return a remote_access_refresh_token"
            )

        _write_secret_text_file(args.token_file, remote_refresh_token)
        session_payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "brand": args.brand,
            "username": username,
            "account": account,
            "activation_token": activation_token,
            "activation_lookup": activation_lookup,
            "confirmation_token": confirmation_token,
            "ble_device": selected_device.to_dict(),
            "mobile_device_name": mobile_device_name,
            "token_file": str(args.token_file),
            "pairing_result": payload,
        }
        if args.session_file:
            _write_session_file(args.session_file, session_payload)
        if args.json_output:
            print(_json_dump(session_payload))
        else:
            print(f"Refresh token written to: {args.token_file}")
            if args.session_file:
                print(f"Session details written to: {args.session_file}")
        return 0
    finally:
        await client._session.close()


async def async_main(args: argparse.Namespace) -> int:
    if args.command == "wizard":
        return await command_wizard(args)
    if args.command == "login":
        return await command_login(args)
    if args.command == "inspect-activation":
        return await command_inspect_activation(args)
    if args.command == "validate-remote-refresh":
        return await command_validate_remote_refresh(args)
    if args.command == "extract-remote-refresh":
        return await command_extract_remote_refresh(args)
    if args.command == "ble-scan":
        return await command_ble_scan(args)
    if args.command == "ble-probe":
        return await command_ble_probe(args)
    if args.command == "tls-self-test":
        return await command_tls_self_test(args)
    if args.command == "scu-tls-probe":
        return await command_scu_tls_probe(args)
    if args.command == "scu-pair-mobile":
        return await command_scu_pair_mobile(args)
    if args.command == "mint-remote-refresh":
        return await command_mint_remote_refresh(args)
    raise HymerTokenToolError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(async_main(args))
    except (
        HymerTokenToolError,
        HymerAuthError,
        BleSupportError,
        ScuBleSessionError,
        TlsSupportError,
    ) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
