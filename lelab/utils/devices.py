"""Device cleanup helpers for LeRobot hardware wrappers.

Serial ports are a trust boundary on Windows: if a normal disconnect fails
while disabling torque, the COM handle can stay open until the Python process
exits. These helpers preserve LeRobot's normal disconnect behavior, then force
close the underlying port/cameras as a last resort.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any


def safe_disconnect_device(device: Any, logger: logging.Logger, context: str = "cleanup") -> None:
    """Disconnect a LeRobot device and force-release resources on failure."""
    if device is None:
        return

    try:
        device.disconnect()
        return
    except Exception as exc:
        logger.warning("Error disconnecting device during %s: %s", context, exc)

    _force_close_device_resources(device, logger)


def _force_close_device_resources(device: Any, logger: logging.Logger) -> None:
    """Best-effort release for serial/camera resources after disconnect fails."""
    bus = getattr(device, "bus", None)
    port_handler = getattr(bus, "port_handler", None)
    if port_handler is not None:
        with suppress(Exception):
            port_handler.clearPort()
        with suppress(Exception):
            port_handler.is_using = False
        try:
            port_handler.closePort()
            logger.info("Force-closed serial port after disconnect failure")
        except Exception as exc:
            logger.warning("Failed to force-close serial port after disconnect failure: %s", exc)

    cameras = getattr(device, "cameras", None)
    if isinstance(cameras, dict):
        for cam in cameras.values():
            try:
                cam.disconnect()
            except Exception as exc:
                logger.warning("Failed to disconnect camera after device cleanup failure: %s", exc)


def friendly_hint(error_text: str | None) -> str | None:
    """A plain-language, actionable headline for the common SO-101 failures."""
    if not error_text:
        return None
    low = error_text.lower()
    if "overload" in low or "torque_enable" in low:
        return (
            "A motor overloaded — usually the gripper holding an object too hard. Release the object / "
            "open the gripper and power-cycle the arm before trying again."
        )
    if "missing motor ids" in low or "motor check failed" in low:
        return (
            "A follower motor isn't responding (often the gripper, id 6). If a skill was holding an object "
            "it likely overloaded — remove it, power-cycle the arm, then try teleoperation first."
        )
    if "could not connect" in low or "failed to connect" in low or "not connected" in low:
        return "Couldn't connect to the arm — make sure it's plugged in, powered on, and on the right port."
    if "frame is too old" in low or "no frame" in low or "frame timeout" in low:
        return (
            "A camera can't keep up — frames are arriving too slowly. Lower its resolution/FPS, "
            "set FOURCC=MJPG, and close other heavy apps, then try again."
        )
    if "failed to set capture_" in low or "actual_width" in low or "actual_height" in low:
        return "A camera doesn't support the configured resolution — open camera settings and click Auto."
    if "permission" in low and ("port" in low or "com" in low):
        return "Couldn't open the serial port — close anything else using it, or run `lelab --stop`."
    return None

