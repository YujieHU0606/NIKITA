"""First reverse-bias I-V sweep for a SPAD using a Keithley 2400.

Connection:
    PC USB -> Prologix GPIB-USB -> Keithley 2400
    Keithley HI -> SPAD Pin 7 -> SPAD -> Pin 6 -> 100 kOhm -> Keithley LO

Before running, confirm COM_PORT and GPIB_ADDRESS below. The program keeps the
output off until the user types RUN, applies 0 V first, limits current to 2 mA,
stops at 30 V (or earlier near compliance), and always tries to switch the
Keithley output off when it finishes or encounters an error.
"""

from __future__ import annotations

import csv
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import serial
from serial.tools import list_ports


#  Confirm these two values before running
COM_PORT = "COM4"          # Check Windows Device Manager -> Ports (COM & LPT)
GPIB_ADDRESS = 11          # Shown on the Keithley front panel at power-up


START_V = 10
STOP_V = 30
STEP_V = 0.1
CURRENT_COMPLIANCE_A = 0.002       # 2 mA
SOFTWARE_STOP_CURRENT_A = 0.0018   # Stop early at 90% of compliance
SETTLE_TIME_S = 0.2

BAUD_RATE = 9600
SERIAL_TIMEOUT_S = 5
OUTPUT_DIR = Path(__file__).resolve().parent / "iv_results"


def available_ports() -> list[str]:
    ports = list(list_ports.comports())
    if not ports:
        return ["  (no serial ports detected)"]
    return [f"  {p.device}: {p.description}" for p in ports]


def safe_name(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", text.strip()).strip("._")
    return cleaned or f"spad_iv_{datetime.now():%Y%m%d_%H%M%S}"


class Keithley2400:
    def __init__(self, port: str, gpib_address: int) -> None:
        self.serial = serial.Serial(
            port=port,
            baudrate=BAUD_RATE,
            timeout=SERIAL_TIMEOUT_S,
            write_timeout=SERIAL_TIMEOUT_S,
        )
        self.gpib_address = gpib_address

    def controller_write(self, command: str) -> None:
        self.serial.write((command + "\r").encode("ascii"))
        self.serial.flush()
        time.sleep(0.05)

    def instrument_write(self, command: str) -> None:
        self.serial.write((command + "\r").encode("ascii"))
        self.serial.flush()
        time.sleep(0.05)

    def query(self, command: str) -> str:
        self.serial.reset_input_buffer()
        self.instrument_write(command)
        self.controller_write("++read eoi")
        response = self.serial.readline().decode("ascii", errors="replace").strip()
        if not response:
            raise RuntimeError(f"No reply from the Keithley for command: {command}")
        return response

    def connect_and_identify(self) -> str:
        self.serial.reset_input_buffer()
        self.serial.reset_output_buffer()
        self.controller_write("++mode 1")
        self.controller_write("++auto 0")
        self.controller_write("++eoi 1")
        self.controller_write("++eos 3")
        # Prologix firmware accepts 1-3000 ms for this controller-side timeout.
        self.controller_write("++read_tmo_ms 3000")
        self.controller_write(f"++addr {self.gpib_address}")

        identity = self.query("*IDN?")
        if "2400" not in identity.upper():
            raise RuntimeError(
                "The connected instrument did not identify as a Keithley 2400: "
                + identity
            )
        return identity

    def configure(self) -> None:
        self.instrument_write("*RST")
        self.instrument_write("*CLS")
        self.instrument_write(":OUTP OFF")
        self.instrument_write(":SOUR:FUNC VOLT")
        self.instrument_write(":SOUR:VOLT:MODE FIX")
        self.instrument_write(":SOUR:VOLT:RANG 200")
        self.instrument_write(":SOUR:VOLT 0")
        self.instrument_write(":SENS:FUNC 'CURR:DC'")
        self.instrument_write(":SENS:CURR:RANG:AUTO ON")
        self.instrument_write(f":SENS:CURR:PROT {CURRENT_COMPLIANCE_A}")
        self.instrument_write(":SENS:CURR:NPLC 1")
        self.instrument_write(":SYST:RSEN OFF")
        self.instrument_write(":TRIG:COUN 1")
        self.instrument_write(":FORM:ELEM VOLT,CURR")

    def output_on_at_zero(self) -> None:
        self.instrument_write(":SOUR:VOLT 0")
        self.instrument_write(":OUTP ON")

    def measure_at(self, requested_voltage: float) -> tuple[float, float]:
        self.instrument_write(f":SOUR:VOLT {requested_voltage:.6f}")
        time.sleep(SETTLE_TIME_S)
        response = self.query(":READ?")
        fields = response.split(",")
        if len(fields) < 2:
            raise RuntimeError(f"Unexpected Keithley response: {response!r}")
        return float(fields[0]), float(fields[1])

    def shutdown(self) -> None:
        # Output OFF is sent first so an error cannot intentionally leave bias on.
        try:
            self.instrument_write(":OUTP OFF")
            self.instrument_write(":SOUR:VOLT 0")
        finally:
            self.serial.close()


def sweep_values(start: float, stop: float, step: float):
    count = int(round((stop - start) / step))
    for index in range(count + 1):
        yield round(start + index * step, 10)


def make_plot(voltages: list[float], currents: list[float], output: Path) -> None:

    fig, ax = plt.subplots(figsize=(11.5, 5))
    ax.plot(voltages, currents, "o-", markersize=2)
    ax.set_title("SPAD reverse I-V (linear scale)")
    ax.set_xlabel("Applied voltage (V)")
    ax.set_ylabel("Measured current (A)")
    ax.grid(True)


    fig.tight_layout()
    fig.savefig(output, dpi=200, bbox_inches="tight")
    plt.show()


def main() -> int:
    print("Detected serial ports:")
    print("\n".join(available_ports()))
    print()
    print("Planned sweep:")
    print(f"  COM port:       {COM_PORT}")
    print(f"  GPIB address:   {GPIB_ADDRESS}")
    print(f"  Voltage:        {START_V:.1f} V to {STOP_V:.1f} V")
    print(f"  Step:           {STEP_V:.1f} V")
    print(f"  Current limit:  {CURRENT_COMPLIANCE_A * 1000:.1f} mA")
    print("  Required:       100 kOhm series resistor and closed dark box")
    print()

    confirmation = input(
        "With the Keithley OUTPUT still OFF, type RUN to connect and begin: "
    ).strip()
    if confirmation != "RUN":
        print("Cancelled. No sweep was started.")
        return 0

    filename = safe_name(input("Data name (for example SPAD100um_01): "))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / f"{filename}.csv"
    png_path = OUTPUT_DIR / f"{filename}.png"

    instrument: Keithley2400 | None = None
    voltages: list[float] = []
    currents: list[float] = []
    return_code = 0

    try:
        instrument = Keithley2400(COM_PORT, GPIB_ADDRESS)
        identity = instrument.connect_and_identify()
        print(f"Connected: {identity}")
        instrument.configure()

        with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["requested_voltage_V", "measured_voltage_V", "current_A"])
            csv_file.flush()

            instrument.output_on_at_zero()
            print("Sweep started. Press Ctrl+C to stop; output will be switched off.")

            for requested_voltage in sweep_values(START_V, STOP_V, STEP_V):
                measured_voltage, current = instrument.measure_at(requested_voltage)
                voltages.append(measured_voltage)
                currents.append(current)
                writer.writerow([requested_voltage, measured_voltage, current])
                csv_file.flush()
                print(
                    f"{requested_voltage:5.1f} V requested | "
                    f"{measured_voltage:9.5f} V measured | {current:+.6e} A"
                )

                if abs(current) >= SOFTWARE_STOP_CURRENT_A:
                    print(
                        "Software stop: measured current reached 90% of the "
                        "2 mA compliance limit."
                    )
                    break

    except KeyboardInterrupt:
        print("\nStopped by user.")
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        print(
            "Check that the Keithley front-panel OUTPUT indicator is OFF before "
            "touching the circuit.",
            file=sys.stderr,
        )
        return_code = 1
    finally:
        if instrument is not None:
            try:
                instrument.shutdown()
                print("Keithley output command sent: OFF; programmed voltage: 0 V.")
            except Exception as shutdown_error:
                print(
                    f"WARNING: automatic shutdown command failed: {shutdown_error}",
                    file=sys.stderr,
                )
                print(
                    "Press the Keithley front-panel OUTPUT ON/OFF button now and "
                    "confirm the OUTPUT indicator is off.",
                    file=sys.stderr,
                )

    if voltages:
        print(f"CSV saved: {csv_path}")
        make_plot(voltages, currents, png_path)
        print(f"Plot saved: {png_path}")
    else:
        print("No measurement points were received; no plot was created.")

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())