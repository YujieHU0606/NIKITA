import csv
from datetime import datetime
from pathlib import Path
import threading
import time

import serial


# Serial settings for the FPGA HASH output.
HASH_PORT = "COM3"
BAUDRATE = 115200
SERIAL_TIMEOUT = 0.05
STABILISATION_SECONDS = 20


# Shared buffers used by the serial-reader thread.
buffer_lock = threading.Lock()
unused_hash_bytes = bytearray()
all_hash_bytes = bytearray()
stop_event = threading.Event()
reader_error = []


print("Keithley 27.6 V MUST BE OFF")
print()
print(f"Opening {HASH_PORT} for FPGA HASH data...")

hash_serial = serial.Serial(
    port=HASH_PORT,
    baudrate=BAUDRATE,
    bytesize=serial.EIGHTBITS,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=SERIAL_TIMEOUT
)

hash_serial.reset_input_buffer()

print(f"{HASH_PORT} opened successfully.")
print()

input(
    "Turn Keithley 27.6 V ON now, "
    "then press Enter: "
)

print()
print(
    f"Stabilising for "
    f"{STABILISATION_SECONDS} seconds..."
)

for remaining in range(
    STABILISATION_SECONDS,
    0,
    -1
):
    if remaining == STABILISATION_SECONDS or remaining <= 5:
        print(f"Remaining: {remaining} s")

    time.sleep(1)


# Discard HASH data produced during stabilisation.
hash_serial.reset_input_buffer()

with buffer_lock:
    unused_hash_bytes.clear()
    all_hash_bytes.clear()


def read_hash_stream():
    try:
        while not stop_event.is_set():
            waiting = hash_serial.in_waiting

            chunk = hash_serial.read(
                waiting if waiting > 0 else 1
            )

            if chunk:
                with buffer_lock:
                    all_hash_bytes.extend(chunk)
                    unused_hash_bytes.extend(chunk)

    except Exception as error:
        reader_error.append(error)
        stop_event.set()


reader_thread = threading.Thread(
    target=read_hash_stream,
    daemon=True
)

start_time = time.time()
reader_thread.start()

print()
print("Live FPGA HASH stream started.")
print("Enter 32 to take the next 32 unused bits.")
print("Enter 64 to take the next 64 unused bits.")
print("Enter Q to stop the demonstration.")

request_records = []
request_number = 0
consumed_bits = 0


def take_next_word(width):
    required_bytes = width // 8

    while not stop_event.is_set():
        with buffer_lock:
            if len(unused_hash_bytes) >= required_bytes:
                word_bytes = bytes(
                    unused_hash_bytes[:required_bytes]
                )

                del unused_hash_bytes[:required_bytes]

                remaining_bits = (
                    len(unused_hash_bytes)
                    * 8
                )

                total_received_bits = (
                    len(all_hash_bytes)
                    * 8
                )

                return (
                    word_bytes,
                    remaining_bits,
                    total_received_bits
                )

        time.sleep(0.005)

    if reader_error:
        raise RuntimeError(
            f"Serial reader stopped: {reader_error[0]}"
        )

    raise RuntimeError(
        "The HASH stream stopped before enough bits arrived."
    )


try:
    while True:
        print()

        command = input(
            "Request 32 or 64 bits: "
        ).strip().lower()

        if command == "q":
            break

        if command not in {"32", "64"}:
            print("Please enter 32, 64, or Q.")
            continue

        width = int(command)

        (
            word_bytes,
            remaining_bits,
            total_received_bits
        ) = take_next_word(width)

        request_number += 1

        start_bit = consumed_bits + 1
        consumed_bits += width
        end_bit = consumed_bits

        binary_value = "".join(
            f"{byte:08b}"
            for byte in word_bytes
        )

        integer_value = int.from_bytes(
            word_bytes,
            byteorder="big",
            signed=False
        )

        hexadecimal_value = (
            "0x"
            + word_bytes.hex().upper()
        )

        elapsed_seconds = (
            time.time()
            - start_time
        )

        print()
        print(f"Request number     : {request_number}")
        print(f"Requested width    : {width} bits")
        print(
            f"Consumed positions : "
            f"{start_bit:,} to {end_bit:,}"
        )
        print(f"Binary             : {binary_value}")
        print(f"Hexadecimal        : {hexadecimal_value}")
        print(f"Unsigned decimal   : {integer_value}")
        print(
            f"Unused buffered bits: "
            f"{remaining_bits:,}"
        )
        print(
            f"Total received bits : "
            f"{total_received_bits:,}"
        )

        request_records.append(
            {
                "request_number": request_number,
                "elapsed_seconds": f"{elapsed_seconds:.6f}",
                "word_size_bits": width,
                "start_bit": start_bit,
                "end_bit": end_bit,
                "binary": binary_value,
                "hexadecimal": hexadecimal_value,
                "unsigned_decimal": str(integer_value)
            }
        )

except KeyboardInterrupt:
    print()
    print("Keyboard interruption received.")

finally:
    stop_event.set()
    reader_thread.join(timeout=1.0)
    hash_serial.close()


with buffer_lock:
    captured_bytes = bytes(all_hash_bytes)


timestamp = datetime.now().strftime(
    "%Y%m%d_%H%M%S"
)

output_directory = Path(__file__).resolve().parent

binary_filename = output_directory / (
    f"LIVE_HASH_{timestamp}.bin"
)

text_filename = output_directory / (
    f"LIVE_HASH_{timestamp}.txt"
)

request_filename = output_directory / (
    f"LIVE_RANDOM_WORDS_{timestamp}.csv"
)


binary_filename.write_bytes(
    captured_bytes
)

with text_filename.open(
    "w",
    encoding="ascii",
    newline="\n"
) as text_file:
    for byte in captured_bytes:
        for bit_position in range(7, -1, -1):
            bit = (
                byte >> bit_position
            ) & 1

            text_file.write(
                f"{bit}\n"
            )


request_fields = [
    "request_number",
    "elapsed_seconds",
    "word_size_bits",
    "start_bit",
    "end_bit",
    "binary",
    "hexadecimal",
    "unsigned_decimal"
]

with request_filename.open(
    "w",
    newline="",
    encoding="utf-8-sig"
) as csv_file:
    writer = csv.DictWriter(
        csv_file,
        fieldnames=request_fields
    )

    writer.writeheader()
    writer.writerows(request_records)


print()
print("Live demonstration stopped.")
print(f"Total HASH bits received : {len(captured_bytes) * 8:,}")
print(f"Random words requested   : {request_number:,}")
print(f"Saved HASH binary file   : {binary_filename.name}")
print(f"Saved HASH text file     : {text_filename.name}")
print(f"Saved request log        : {request_filename.name}")
print()
print("Turn Keithley 27.6 V OFF now.")