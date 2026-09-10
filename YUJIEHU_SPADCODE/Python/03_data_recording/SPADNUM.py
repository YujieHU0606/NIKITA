import serial
import time
from datetime import datetime


RAW_PORT = "COM5"
FPGA_PORT = "COM3"

BAUD = 115200

STABILIZATION_SECONDS = 20
CAPTURE_SECONDS = 280


print("27.5 V MUST BE OFF")
print()

print("Opening COM5 (RedBoard RAW)...")

raw_ser = serial.Serial(
    port=RAW_PORT,
    baudrate=BAUD,
    bytesize=8,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=0.05
)

print("Opening COM3 (FPGA HASHING)...")

fpga_ser = serial.Serial(
    port=FPGA_PORT,
    baudrate=BAUD,
    bytesize=8,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=0.05
)


# The RedBoard resets when COM5 is opened.
print("Waiting for RedBoard reset...")
time.sleep(2)

raw_ser.reset_input_buffer()
fpga_ser.reset_input_buffer()


print()
print("Turn Keithley 27.5 V ON now.")

input(
    "After 27.5 V is ON, press Enter to start "
    "the 20 s stabilisation period..."
)


# Data received during stabilisation is discarded.
# Both serial ports are continuously drained to prevent overflow.
print()
print(f"Stabilising for {STABILIZATION_SECONDS} seconds...")
print("RAW and HASH data during this period will be discarded.")
print()

stabilization_start = time.perf_counter()
last_report = -1

while (
    time.perf_counter() - stabilization_start
    < STABILIZATION_SECONDS
):

    raw_waiting = raw_ser.in_waiting

    if raw_waiting > 0:
        raw_ser.read(raw_waiting)

    hash_waiting = fpga_ser.in_waiting

    if hash_waiting > 0:
        fpga_ser.read(hash_waiting)

    elapsed = time.perf_counter() - stabilization_start
    remaining = int(STABILIZATION_SECONDS - elapsed)

    if remaining % 10 == 0 and remaining != last_report:
        print(f"Stabilisation remaining: {remaining} s")
        last_report = remaining


# Remove any data received immediately before stabilisation ended.
raw_ser.reset_input_buffer()
fpga_ser.reset_input_buffer()


print()
print("Stabilisation complete.")
print("Starting formal acquisition now.")
print()


# Generate filenames when formal acquisition begins.
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

raw_txt_filename = f"SPAD_RAW_{timestamp}.txt"
hash_txt_filename = f"SPAD_HASH_FPGA_{timestamp}.txt"


raw_data = bytearray()
hash_data = bytearray()


print("27.5 V MUST BE ON")

print(
    f"Capturing RAW + FPGA HASHING "
    f"for {CAPTURE_SECONDS} seconds..."
)

print()


start_time = time.perf_counter()


try:

    while (
        time.perf_counter() - start_time
        < CAPTURE_SECONDS
    ):

        raw_waiting = raw_ser.in_waiting

        if raw_waiting > 0:

            raw_data.extend(
                raw_ser.read(raw_waiting)
            )

        hash_waiting = fpga_ser.in_waiting

        if hash_waiting > 0:

            hash_data.extend(
                fpga_ser.read(hash_waiting)
            )

finally:

    raw_ser.close()
    fpga_ser.close()


actual_time = time.perf_counter() - start_time


print()
print("Formal acquisition ended.")
print("Turn Keithley 27.5 V OFF now.")
print()


# Create the text representation of every possible byte.
# Bits are written from bit 7 to bit 0.
byte_to_bit_lines = []

for byte_value in range(256):

    bit_lines = ""

    for bit_position in range(7, -1, -1):

        bit = (
            byte_value >> bit_position
        ) & 1

        bit_lines += f"{bit}\n"

    byte_to_bit_lines.append(bit_lines)


# Save one binary bit per line for NIST testing.
def save_nist_txt(data, filename):

    byte_chunk_size = 10000

    with open(
        filename,
        "w",
        encoding="ascii",
        newline="\n"
    ) as file:

        for start in range(
            0,
            len(data),
            byte_chunk_size
        ):

            chunk = data[
                start:start + byte_chunk_size
            ]

            text_chunk = "".join(
                byte_to_bit_lines[byte]
                for byte in chunk
            )

            file.write(text_chunk)


print("Saving RAW text file...")

save_nist_txt(
    raw_data,
    raw_txt_filename
)


print("Saving HASH text file...")

save_nist_txt(
    hash_data,
    hash_txt_filename
)


# Calculate the number of bits, zeros and ones.
raw_total_bits = len(raw_data) * 8

raw_ones = sum(
    byte.bit_count()
    for byte in raw_data
)

raw_zeros = raw_total_bits - raw_ones


hash_total_bits = len(hash_data) * 8

hash_ones = sum(
    byte.bit_count()
    for byte in hash_data
)

hash_zeros = hash_total_bits - hash_ones


print()
print("RedBoard RAW")

print(f"Bytes : {len(raw_data):,}")
print(f"Bits  : {raw_total_bits:,}")
print(f"0     : {raw_zeros:,}")
print(f"1     : {raw_ones:,}")


if raw_total_bits > 0:

    raw_ratio = (
        100.0
        * raw_ones
        / raw_total_bits
    )

    print(f"P(1)  : {raw_ratio:.4f}%")


print()
print("FPGA HASHING")

print(f"Bytes : {len(hash_data):,}")
print(f"Bits  : {hash_total_bits:,}")
print(f"0     : {hash_zeros:,}")
print(f"1     : {hash_ones:,}")


if hash_total_bits > 0:

    hash_ratio = (
        100.0
        * hash_ones
        / hash_total_bits
    )

    print(f"P(1)  : {hash_ratio:.4f}%")


# Calculate the RAW and HASH data rates.
print()
print("Data rate")

print(
    f"Actual capture time : "
    f"{actual_time:.2f} s"
)


if actual_time > 0:

    raw_rate = (
        raw_total_bits
        / actual_time
    )

    hash_rate = (
        hash_total_bits
        / actual_time
    )

    print(
        f"RAW bit rate        : "
        f"{raw_rate:.2f} bit/s"
    )

    print(
        f"FPGA HASH bit rate  : "
        f"{hash_rate:.2f} bit/s"
    )


# Display the names of the two saved text files.
print()
print("Saved files")

print(
    f"RAW NIST text   : "
    f"{raw_txt_filename}"
)

print(
    f"HASH NIST text  : "
    f"{hash_txt_filename}"
)


print()
print("Done.")