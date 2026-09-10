import serial
import time
import matplotlib.pyplot as plt


# Settings

PORT = "COM4"
BAUDRATE = 115200
GPIB_ADDRESS = 1
POINTS = 2500


# Open serial port

print("Opening COM4...")

ser = serial.Serial(
    port=PORT,
    baudrate=BAUDRATE,
    timeout=3.0
)

time.sleep(1)


def send(command):
    ser.write((command + "\n").encode("ascii"))
    ser.flush()
    time.sleep(0.03)


def read_text():
    send("++read eoi")
    data = ser.readline()

    return data.decode(
        "ascii",
        errors="ignore"
    ).strip()


def query(command):
    send(command)
    return read_text()


# Configure Prologix

print("Configuring Prologix...")

send("++mode 1")
send("++auto 0")
send("++addr {}".format(GPIB_ADDRESS))
send("++eoi 1")
send("++eos 2")
send("++read_tmo_ms 3000")

ser.reset_input_buffer()


# Check oscilloscope connection

idn = query("*IDN?")

print("Connected to:")
print(idn)
print()


# Enable CH1 and CH2

send("SELECT:CH1 ON")
send("SELECT:CH2 ON")

send("DATA:START 1")
send("DATA:STOP {}".format(POINTS))
send("DATA:ENC ASCII")


# Store channel parameters

channel_parameters = {
    "CH1": {
        "scale": None,
        "x_increment": 0.0,
        "x_zero": 0.0,
        "y_multiplier": 1.0,
        "y_zero": 0.0,
        "y_offset": 0.0
    },
    "CH2": {
        "scale": None,
        "x_increment": 0.0,
        "x_zero": 0.0,
        "y_multiplier": 1.0,
        "y_zero": 0.0,
        "y_offset": 0.0
    }
}


def read_waveform_parameters(channel):
    send("DATA:SOURCE {}".format(channel))

    parameters = channel_parameters[channel]

    parameters["x_increment"] = float(
        query("WFMPRE:XINCR?")
    )

    parameters["x_zero"] = float(
        query("WFMPRE:XZERO?")
    )

    parameters["y_multiplier"] = float(
        query("WFMPRE:YMULT?")
    )

    parameters["y_zero"] = float(
        query("WFMPRE:YZERO?")
    )

    parameters["y_offset"] = float(
        query("WFMPRE:YOFF?")
    )


def read_channel_waveform(channel):
    parameters = channel_parameters[channel]

    send("DATA:SOURCE {}".format(channel))

    current_scale = float(
        query("{}:SCALE?".format(channel))
    )

    current_position = float(
        query("{}:POSITION?".format(channel))
    )

    # Update conversion parameters if V/div has changed.
    if parameters["scale"] != current_scale:
        parameters["scale"] = current_scale
        read_waveform_parameters(channel)
        send("DATA:SOURCE {}".format(channel))

    raw_text = query("CURVE?")

    if not raw_text:
        raise ValueError(
            "No waveform data received from {}.".format(channel)
        )

    adc_values = []

    for value in raw_text.split(","):
        value = value.strip()

        if value:
            adc_values.append(float(value))

    if len(adc_values) == 0:
        raise ValueError(
            "No valid waveform points received from {}.".format(channel)
        )

    # Convert ADC values to volts.
    voltage = []

    for value in adc_values:
        converted_voltage = (
            (value - parameters["y_offset"])
            * parameters["y_multiplier"]
            + parameters["y_zero"]
        )

        voltage.append(converted_voltage)

    # Create the time axis.
    time_axis = []

    for index in range(len(voltage)):
        current_time = (
            parameters["x_zero"]
            + index * parameters["x_increment"]
        )

        time_axis.append(current_time)

    # Calculate the displayed voltage range.
    y_top = (4.0 - current_position) * current_scale
    y_bottom = (-4.0 - current_position) * current_scale

    return (
        time_axis,
        voltage,
        current_scale,
        current_position,
        y_bottom,
        y_top
    )


# Read initial channel parameters

print("Reading CH1 parameters...")
read_waveform_parameters("CH1")

print("Reading CH2 parameters...")
read_waveform_parameters("CH2")

print()


# Prepare live graph

plt.ion()

fig, (ax1, ax2) = plt.subplots(
    2,
    1,
    figsize=(11, 8),
    sharex=True
)

line_ch1, = ax1.plot(
    [],
    [],
    color="gold",
    linewidth=1.0,
    label="CH1"
)

line_ch2, = ax2.plot(
    [],
    [],
    color="deepskyblue",
    linewidth=1.0,
    label="CH2"
)

ax1.set_title("TDS210 - CH1")
ax1.set_ylabel("Voltage (V)")
ax1.grid(True)
ax1.legend(loc="upper right")

ax2.set_title("TDS210 - CH2")
ax2.set_xlabel("Time (s)")
ax2.set_ylabel("Voltage (V)")
ax2.grid(True)
ax2.legend(loc="upper right")

fig.suptitle("TDS210 Dual-Channel Live Waveform")

plt.tight_layout()
plt.show(block=False)


# Live loop

print("CH1 and CH2 live waveform started.")
print("Close the graph window or press Ctrl+C to stop.")
print()


try:
    while plt.fignum_exists(fig.number):

        try:
            (
                time_ch1,
                voltage_ch1,
                scale_ch1,
                position_ch1,
                bottom_ch1,
                top_ch1
            ) = read_channel_waveform("CH1")

            (
                time_ch2,
                voltage_ch2,
                scale_ch2,
                position_ch2,
                bottom_ch2,
                top_ch2
            ) = read_channel_waveform("CH2")

        except Exception as error:
            print("Could not read waveform:", error)
            continue

        line_ch1.set_data(time_ch1, voltage_ch1)
        ax1.set_ylim(bottom_ch1, top_ch1)

        ax1.set_title(
            "CH1   {:.3g} V/div   Position {:.2f} div".format(
                scale_ch1,
                position_ch1
            )
        )

        line_ch2.set_data(time_ch2, voltage_ch2)
        ax2.set_ylim(bottom_ch2, top_ch2)

        ax2.set_title(
            "CH2   {:.3g} V/div   Position {:.2f} div".format(
                scale_ch2,
                position_ch2
            )
        )

        time_start = min(
            time_ch1[0],
            time_ch2[0]
        )

        time_end = max(
            time_ch1[-1],
            time_ch2[-1]
        )

        ax2.set_xlim(
            time_start,
            time_end
        )

        fig.suptitle(
            "TDS210 Dual-Channel Live Waveform   "
            + time.strftime("%H:%M:%S")
        )

        fig.canvas.draw_idle()
        fig.canvas.flush_events()

        plt.pause(0.05)


except KeyboardInterrupt:
    print()
    print("Stopped by user.")


finally:
    ser.close()
    plt.ioff()

    print("COM4 closed.")