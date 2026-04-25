import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from models.models import db, PedalSession, SessionData


# Support two formats:
# 1. "Voltage: X V | Current: Y A" (original format)
# 2. "V=X V I=Y A" (ESP32 simple format)
_LINE_RE = re.compile(
    r"(?:RPM:\s*(?P<rpm>-?\d+(?:\.\d+)?)\s*\|\s*)?"
    r"(?:"
    r"Voltage:\s*(?P<voltage>-?\d+(?:\.\d+)?)\s*V\s*\|\s*Current:\s*(?P<current>-?\d+(?:\.\d+)?)\s*A"
    r"|"
    r"V=\s*(?P<voltage2>-?\d+(?:\.\d+)?)\s*V\s*I=\s*(?P<current2>-?\d+(?:\.\d+)?)\s*A"
    r")",
    re.IGNORECASE,
)


@dataclass
class Esp32BridgeConfig:
    port: str
    baud: int = 115200
    enabled: bool = True


def _auto_detect_port() -> str | None:
    """
    Best-effort serial port auto-detection for common ESP32 USB-UART chips.
    If exactly one likely port is found, return it (e.g., "COM4").
    """
    try:
        from serial.tools import list_ports  # type: ignore
    except Exception:
        return None

    ports = list(list_ports.comports())
    if not ports:
        return None

    def _score(p) -> int:
        hay = " ".join(
            str(x or "")
            for x in [p.device, getattr(p, "description", ""), getattr(p, "manufacturer", ""), getattr(p, "hwid", "")]
        ).lower()
        score = 0
        for needle in ["ch9102", "wch", "cp210", "silicon labs", "usb serial", "uart", "esp32"]:
            if needle in hay:
                score += 1
        return score

    ranked = sorted(((p.device, _score(p)) for p in ports), key=lambda t: t[1], reverse=True)
    best_device, best_score = ranked[0]

    # Only trust auto-detect if it looks like a serial adapter and isn't ambiguous
    top = [d for d, s in ranked if s == best_score and best_score > 0]
    if len(top) == 1:
        return best_device

    # If there is exactly one port on the system, also accept it
    if len(ports) == 1:
        return ports[0].device

    return None


def _get_config() -> Esp32BridgeConfig | None:
    port = (os.environ.get("ESP32_SERIAL_PORT") or "").strip()
    if not port:
        port = _auto_detect_port() or ""
        if not port:
            return None

    baud_raw = (os.environ.get("ESP32_SERIAL_BAUD") or "").strip()
    baud = 115200
    if baud_raw:
        try:
            baud = int(baud_raw)
        except ValueError:
            baud = 115200

    enabled = (os.environ.get("ESP32_SERIAL_ENABLED") or "1").strip().lower() not in {"0", "false", "no"}
    return Esp32BridgeConfig(port=port, baud=baud, enabled=enabled)


def _update_active_session(rpm: float, voltage: float, current: float) -> bool:
    """
    Push one reading into the most recent active session.
    Returns True if an active session existed and was updated.
    """
    session = (
        PedalSession.query.filter(PedalSession.end_time.is_(None))
        .order_by(PedalSession.start_time.desc())
        .first()
    )
    if not session:
        return False

    # Append a detailed point (raw == smoothed here; the API applies moving average, but this keeps UI live)
    point = SessionData(
        session_id=session.id,
        raw_rpm=rpm,
        raw_voltage=voltage,
        raw_current=current,
        smoothed_rpm=rpm,
        smoothed_voltage=voltage,
        smoothed_current=current,
        power_w=voltage * current,
    )
    db.session.add(point)

    # Update session "latest" and rolling averages
    session._rpm_sum += rpm
    session._voltage_sum += voltage
    session._current_sum += current
    session._data_points += 1

    session.avg_rpm = session._rpm_sum / session._data_points
    session.avg_voltage = session._voltage_sum / session._data_points
    session.avg_current = session._current_sum / session._data_points

    session.raw_rpm = rpm
    session.raw_voltage = voltage
    session.raw_current = current
    session.power_w = voltage * current
    session.last_updated = datetime.utcnow()

    db.session.commit()
    return True


def start_esp32_serial_bridge(app) -> None:
    """
    Starts a background thread that reads ESP32 Serial output like:
      Voltage: 1.23 V | Current: 0.45 A

    Enable by setting:
      ESP32_SERIAL_PORT=COM3   (Windows example)
      ESP32_SERIAL_BAUD=115200 (optional)
    """
    cfg = _get_config()
    if not cfg or not cfg.enabled:
        print("[ESP32] Serial bridge disabled (set ESP32_SERIAL_PORT=COMx to enable).")
        return

    def _runner():
        try:
            import serial  # pyserial
        except Exception as e:
            print(f"[ESP32] pyserial not available; install with `pip install pyserial`. ({e})")
            return

        log_raw = (os.environ.get("ESP32_LOG_RAW") or "").strip().lower() in {"1", "true", "yes"}
        last_print = 0.0

        def _open_serial(port: str):
            # `exclusive=True` helps on Windows (if supported by pyserial version)
            try:
                return serial.Serial(port, cfg.baud, timeout=1, write_timeout=1, exclusive=True)
            except TypeError:
                return serial.Serial(port, cfg.baud, timeout=1, write_timeout=1)

        def _port_candidates() -> list[str]:
            # Try configured port first, then auto-detected port if different.
            candidates: list[str] = []
            if cfg.port:
                candidates.append(cfg.port)
            auto = _auto_detect_port()
            if auto and auto not in candidates:
                candidates.append(auto)
            return candidates or [cfg.port]

        while True:
            for port in _port_candidates():
                try:
                    print(f"[ESP32] Connecting serial {port} @ {cfg.baud} ...")
                    with _open_serial(port) as ser:
                        # Avoid toggling reset lines on some ESP32 USB-UART adapters.
                        # If your board requires DTR/RTS for bootloader, Arduino upload will still work
                        # because it opens the port separately.
                        try:
                            ser.dtr = False
                            ser.rts = False
                        except Exception:
                            pass

                        # Give the device a moment after opening the port
                        time.sleep(0.2)
                        ser.reset_input_buffer()
                        print("[ESP32] Connected. Waiting for lines...")
                        while True:
                            try:
                                raw = ser.readline()
                            except (PermissionError, OSError) as e:
                                # On Windows, transient USB/driver issues can surface here.
                                print(f"[ESP32] Read error on {port}: {e}. Reconnecting...")
                                break
                            if not raw:
                                continue
                            try:
                                line = raw.decode("utf-8", errors="ignore").strip()
                            except Exception:
                                continue

                            if log_raw and line:
                                now = time.time()
                                if now - last_print > 2.0:
                                    print(f"[ESP32] raw: {line}")
                                    last_print = now

                            m = _LINE_RE.search(line)
                            if not m:
                                continue

                            try:
                                # Handle both format groups
                                voltage_str = m.group("voltage") or m.group("voltage2")
                                current_str = m.group("current") or m.group("current2")
                                voltage = float(voltage_str)
                                current = float(current_str)
                                rpm_str = m.group("rpm")
                                if rpm_str is not None:
                                    rpm = float(rpm_str)
                                else:
                                    # Fallback simulated RPM for testing
                                    import random
                                    rpm = random.uniform(40, 120)
                            except ValueError:
                                continue

                            print(f"[DEBUG - Source] RPM Generated: {rpm:.2f} (Simulated: {rpm_str is None})")

                            try:
                                with app.app_context():
                                    updated = _update_active_session(rpm=rpm, voltage=voltage, current=current)
                                now = time.time()
                                if now - last_print > 2.0:
                                    if updated:
                                        print(f"[ESP32] parsed: V={voltage:.3f}V I={current:.3f}A RPM={rpm:.1f} -> dashboard")
                                    else:
                                        print("[ESP32] parsed line but no active session (click Start Session).")
                                    last_print = now
                            except Exception as e:
                                print(f"[ESP32] DB update error: {e}")
                except PermissionError as e:
                    print(
                        f"[ESP32] {port} is busy/locked: {e}. "
                        "Close Arduino Serial Monitor/Plotter and any other app using the port, "
                        "then unplug/replug the ESP32. Retrying in 3s..."
                    )
                    time.sleep(3)
                except FileNotFoundError as e:
                    print(
                        f"[ESP32] {port} disappeared: {e}. "
                        "This usually means the USB device disconnected/reset. "
                        "Unplug/replug the ESP32 (or try a different USB cable/port). Retrying in 2s..."
                    )
                    time.sleep(2)
                except Exception as e:
                    print(f"[ESP32] Serial error on {port}: {e}. Retrying in 2s...")
                    time.sleep(2)

    t = threading.Thread(target=_runner, name="esp32-serial-bridge", daemon=True)
    t.start()

