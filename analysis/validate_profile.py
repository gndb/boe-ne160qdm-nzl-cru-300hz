#!/usr/bin/env python3
"""Validate the published snapshot offline using only the Python standard library.

This script only reads project files. It never queries or changes Windows,
restarts a driver, accesses the network, or verifies physical frame delivery.
JSON and SHA-256 checks establish internal consistency, not authenticity.
"""

import argparse
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys


EDID_HEADER = bytes.fromhex("00 ff ff ff ff ff ff 00")
AXIS_FIELDS = (
    "active", "front_porch", "sync_width", "back_porch", "blanking", "total"
)
RATE_TOLERANCE = Decimal("0.0005")  # Allow a refresh rate rounded to 0.001 Hz.
CLOCK_LIMIT_MHZ = Decimal("1304")  # User-reported trial threshold, not a proven hardware limit.


class ValidationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def object_with(value, keys, label):
    require(isinstance(value, dict), f"{label}: expected an object")
    for key in keys:
        require(key in value, f"{label}: missing {key}")
    return value


def integer(value, label, minimum=0):
    require(type(value) is int and value >= minimum,
            f"{label}: expected an integer >= {minimum}")
    return value


def number(value, label, positive=True):
    require(not isinstance(value, bool) and isinstance(value, (int, Decimal)),
            f"{label}: expected a JSON number")
    result = Decimal(value)
    require(result.is_finite(), f"{label}: number must be finite")
    require(not positive or result > 0, f"{label}: number must be positive")
    return result


def project_file(root, value, label):
    require(isinstance(value, str) and value.strip(), f"{label}: empty path")
    normalized = value.replace("\\", "/")
    relative = PurePosixPath(normalized)
    require(not relative.is_absolute() and not PureWindowsPath(value).drive,
            f"{label}: use a project-relative path")
    require(".." not in relative.parts and ":" not in normalized and "\x00" not in value,
            f"{label}: unsafe path")
    resolved = root.joinpath(*relative.parts).resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValidationError(f"{label}: path escapes the project") from exc
    require(resolved.is_file(), f"{label}: not a file")
    return resolved


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"JSON: duplicate key {key}")
        result[key] = value
    return result


def reject_constant(value):
    raise ValidationError(f"JSON: non-finite number {value}")


def validate_timing(profile, label):
    object_with(profile, ("width", "height", "refresh_hz", "pixel_clock_mhz",
                          "horizontal", "vertical"), label)
    width = integer(profile["width"], f"{label}.width", 1)
    height = integer(profile["height"], f"{label}.height", 1)
    rate = number(profile["refresh_hz"], f"{label}.refresh_hz")
    clock = number(profile["pixel_clock_mhz"], f"{label}.pixel_clock_mhz")
    require(clock <= CLOCK_LIMIT_MHZ, f"{label}: exceeds the 1304 MHz trial threshold")
    require(clock * 1000 == (clock * 1000).to_integral_value(),
            f"{label}: Type VII clock must have 0.001 MHz granularity")
    for name, active in (("horizontal", width), ("vertical", height)):
        axis = object_with(profile[name], AXIS_FIELDS + ("polarity",), f"{label}.{name}")
        for field in AXIS_FIELDS:
            integer(axis[field], f"{label}.{name}.{field}")
        require(axis["active"] == active, f"{label}.{name}: active size disagrees")
        require(axis["sync_width"] > 0, f"{label}.{name}: sync width must be positive")
        require(axis["blanking"] == sum(axis[k] for k in
                ("front_porch", "sync_width", "back_porch")),
                f"{label}.{name}: blanking does not equal the three timing intervals")
        require(axis["total"] == active + axis["blanking"],
                f"{label}.{name}: total does not equal active + blanking")
        require(axis["polarity"] in ("+", "-"), f"{label}.{name}: invalid polarity")
    calculated_rate = clock * 1_000_000 / (
        profile["horizontal"]["total"] * profile["vertical"]["total"])
    require(abs(calculated_rate - rate) <= RATE_TOLERANCE,
            f"{label}: clock / totals disagrees with refresh_hz")


def decode_type_vii(data, label):
    """DisplayID 2.x Type VII: 20 bytes, clock-minus-one in kHz.

    Active, blanking, sync offset and sync width are stored minus one.
    Sync-offset bit 15 is polarity, not part of the offset. Back porch is
    derived from blanking minus front porch and sync width; zero is valid.
    """
    require(len(data) == 20, f"{label}: invalid Type VII descriptor size")
    require(not data[3] & 0x10, f"{label}: interlaced timing is outside this snapshot")
    clock_khz = int.from_bytes(data[0:3], "little") + 1

    def axis(start):
        active = int.from_bytes(data[start:start + 2], "little") + 1
        blank = int.from_bytes(data[start + 2:start + 4], "little") + 1
        offset = int.from_bytes(data[start + 4:start + 6], "little")
        front = (offset & 0x7fff) + 1
        sync = int.from_bytes(data[start + 6:start + 8], "little") + 1
        require(blank >= front + sync, f"{label}: negative back porch")
        return {
            "active": active, "front_porch": front, "sync_width": sync,
            "back_porch": blank - front - sync, "blanking": blank,
            "total": active + blank, "polarity": "+" if offset & 0x8000 else "-",
        }

    horizontal, vertical = axis(4), axis(12)
    return {
        "width": horizontal["active"], "height": vertical["active"],
        "pixel_clock_mhz": Decimal(clock_khz) / 1000,
        "refresh_hz": Decimal(clock_khz) * 1000 / (horizontal["total"] * vertical["total"]),
        "horizontal": horizontal, "vertical": vertical,
    }


def inspect_edid(data, panel_id, label):
    require(len(data) >= 128 and len(data) % 128 == 0, f"{label}: invalid EDID size")
    require(data[:8] == EDID_HEADER, f"{label}: invalid base EDID header")
    require(data[126] + 1 == len(data) // 128,
            f"{label}: extension count does not match the complete file")
    require(data[12:16] == b"\x00" * 4, f"{label}: numeric monitor serial is not zero")
    manufacturer = int.from_bytes(data[8:10], "big")
    identity = "".join(chr(((manufacturer >> shift) & 31) + 64) for shift in (10, 5, 0))
    identity += f"{int.from_bytes(data[10:12], 'little'):04X}"
    require(identity == panel_id, f"{label}: panel ID does not match the manifest")
    for offset in range(54, 126, 18):
        descriptor = data[offset:offset + 18]
        require(not (descriptor[:3] == b"\x00" * 3 and descriptor[3] == 0xff),
                f"{label}: monitor serial descriptor is present")
    timings = []
    for index in range(len(data) // 128):
        block = data[index * 128:(index + 1) * 128]
        require(sum(block) % 256 == 0, f"{label}: block {index} checksum failed")
        if index == 0 or block[0] != 0x70:
            continue
        end = 5 + block[2]
        require(end < 127, f"{label}: invalid DisplayID payload length")
        require(sum(block[1:end + 1]) % 256 == 0,
                f"{label}: DisplayID block {index} internal checksum failed")
        position = 5
        while position < end:
            if not any(block[position:end]):
                break
            require(position + 3 <= end, f"{label}: truncated DisplayID data header")
            tag, revision, length = block[position:position + 3]
            position += 3
            require(position + length <= end, f"{label}: truncated DisplayID data block")
            payload = block[position:position + length]
            if block[1] >> 4 == 2:
                require(tag != 0x20, f"{label}: DisplayID product/serial identification is present")
                if tag == 0x22:
                    require(length > 0 and length % 20 == 0,
                            f"{label}: invalid Type VII timing block length")
                    for start in range(0, length, 20):
                        timings.append(decode_type_vii(payload[start:start + 20], label))
            position += length
    require(timings, f"{label}: no DisplayID 2.x Type VII timings found")
    return timings


def matches(left, right):
    return (left["width"] == right["width"] and left["height"] == right["height"]
            and Decimal(left["pixel_clock_mhz"]) == Decimal(right["pixel_clock_mhz"])
            and abs(Decimal(left["refresh_hz"]) - Decimal(right["refresh_hz"])) <= RATE_TOLERANCE
            and left["horizontal"] == right["horizontal"]
            and left["vertical"] == right["vertical"])


def validate(root, manifest_path):
    with manifest_path.open(encoding="utf-8-sig") as handle:
        manifest = json.load(handle, parse_float=Decimal, parse_constant=reject_constant,
                             object_pairs_hook=unique_object)
    object_with(manifest, ("schema_version", "captured_at_utc", "hardware", "windows_active_mode",
                           "current_profile", "preserved_profiles", "artifacts", "validation"), "manifest")
    require(type(manifest["schema_version"]) is int and manifest["schema_version"] == 1,
            "manifest: unsupported schema version")
    timestamp = manifest["captured_at_utc"]
    require(isinstance(timestamp, str), "captured_at_utc: expected an ISO UTC timestamp")
    captured = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    require(captured.tzinfo is not None and captured.utcoffset() == timedelta(0),
            "captured_at_utc: timestamp must include UTC")
    hardware = object_with(manifest["hardware"], ("system", "cpu", "integrated_gpu", "panel",
                                                  "panel_id", "intel_driver"), "hardware")
    for key in ("system", "cpu", "integrated_gpu", "panel", "panel_id", "intel_driver"):
        require(isinstance(hardware[key], str) and hardware[key].strip(), f"hardware.{key}: empty value")
    require(hardware["panel_id"] == "BOE0D55", "hardware: unexpected panel ID")
    require("13900HX" in hardware["cpu"] and "NE160QDM-NZL" in hardware["panel"],
            "hardware: unexpected CPU or panel")
    require("Intel" in hardware["integrated_gpu"], "hardware: unexpected integrated GPU")
    current = manifest["current_profile"]
    validate_timing(current, "current_profile")
    require((current["width"], current["height"]) == (2560, 1600), "current_profile: wrong resolution")
    require(number(current["refresh_hz"], "current refresh") == 300, "current_profile: not 300 Hz")
    preserved = manifest["preserved_profiles"]
    require(isinstance(preserved, list) and preserved, "preserved_profiles: expected nonempty array")
    profiles = [current]
    for index, profile in enumerate(preserved):
        validate_timing(profile, f"preserved_profiles[{index}]")
        require((profile["width"], profile["height"]) == (2560, 1600),
                f"preserved_profiles[{index}]: wrong native resolution")
        require(not any(matches(profile, existing) for existing in profiles),
                f"preserved_profiles[{index}]: duplicate timing")
        profiles.append(profile)
    mode = object_with(manifest["windows_active_mode"], ("gpu", "width", "height", "refresh_hz", "bpp"),
                       "windows_active_mode")
    require(isinstance(mode["gpu"], str) and "Intel" in mode["gpu"], "saved Windows mode is not Intel")
    require(integer(mode["width"], "saved mode width", 1) == 2560
            and integer(mode["height"], "saved mode height", 1) == 1600
            and number(mode["refresh_hz"], "saved mode refresh") == 300,
            "saved Windows mode is not 2560x1600 at 300 Hz")
    integer(mode["bpp"], "saved mode bpp", 1)
    object_with(manifest["validation"], (), "validation")
    artifacts = object_with(manifest["artifacts"], ("current_edid", "native_reference_edid"), "artifacts")
    hashes = {}
    decoded = {}
    for name in ("current_edid", "native_reference_edid"):
        artifact = object_with(artifacts[name], ("path", "bytes", "sha256"), name)
        path = project_file(root, artifact["path"], f"{name}.path")
        data = path.read_bytes()
        require(len(data) == integer(artifact["bytes"], f"{name}.bytes", 128),
                f"{name}: byte length does not match manifest")
        expected_hash = artifact["sha256"]
        require(isinstance(expected_hash, str) and re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash),
                f"{name}: invalid SHA-256 string")
        digest = hashlib.sha256(data).hexdigest()
        require(digest == expected_hash.lower(), f"{name}: SHA-256 mismatch")
        hashes[name] = digest
        decoded[name] = inspect_edid(data, hardware["panel_id"], name)
    for index, profile in enumerate(profiles):
        require(any(matches(profile, timing) for timing in decoded["current_edid"]),
                f"profile {index}: no exact matching saved Type VII timing (including polarity/porches)")
    require(all(any(matches(timing, profile) for profile in profiles)
                for timing in decoded["current_edid"]),
            "current_edid: contains a Type VII timing not described by the manifest")
    return current, preserved, hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="outputs/profile.json", help="project-relative manifest path")
    args = parser.parse_args()
    try:
        root = Path(__file__).resolve().parent.parent
        manifest_path = project_file(root, args.profile, "manifest path")
        current, preserved, hashes = validate(root, manifest_path)
    except (ValidationError, OSError, ValueError, InvalidOperation) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    horizontal, vertical = current["horizontal"], current["vertical"]
    print("PASS: offline manifest, timing, privacy and EDID checksum consistency")
    print(f"Current: 2560x1600 @ {Decimal(current['refresh_hz']):.3f} Hz; "
          f"{Decimal(current['pixel_clock_mhz']):.3f} MHz; "
          f"H {horizontal['front_porch']}/{horizontal['sync_width']}/{horizontal['back_porch']} "
          f"total {horizontal['total']} {horizontal['polarity']}; "
          f"V {vertical['front_porch']}/{vertical['sync_width']}/{vertical['back_porch']} "
          f"total {vertical['total']} {vertical['polarity']}")
    print("Preserved Hz: " + ", ".join(f"{Decimal(p['refresh_hz']):.3f}" for p in preserved))
    for name, digest in hashes.items():
        print(f"SHA256 {name}: {digest}")
    print("Saved Windows readback checked; live display and physical frame delivery were not tested.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
