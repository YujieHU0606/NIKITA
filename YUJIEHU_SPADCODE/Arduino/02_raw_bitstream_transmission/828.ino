// SPAD RAW BITSTREAM -> FPGA
// FINAL BINARY VERSION
//
// Board: SparkFun RedBoard / ATmega328P
//
// Connection:
// AD8561 Pin 8 -> RedBoard D2
//
// RedBoard D1/TX
//      -> voltage divider
//      -> Nexys A7 JA1
//
// RedBoard GND -> FPGA GND
//
// UART:
// 115200 baud
//
// IMPORTANT:
// This program sends ONLY binary raw bytes.
// NO Serial.print()
// NO Serial.println()
//
// Therefore:
//
// COM5 receives raw binary bytes
// D1/TX sends exactly the same raw binary bytes to FPGA.
//
// Raw-bit rule:
//
// dt1 > dt2 -> 1
// dt1 < dt2 -> 0
// dt1 = dt2 -> discard
//
// Acquisition:
//
// 128 consecutive D2 timestamps
// -> 127 intervals
// -> non-overlapping interval pairs
// -> raw bits
// -> pack every 8 raw bits into one byte
// -> send binary byte
//
// No interval crosses two batches.



const uint8_t SPAD_PIN = 2;
const uint8_t BATCH_SIZE = 128;

// Timestamp storage

volatile uint16_t timestamps[BATCH_SIZE];

volatile uint8_t captureIndex = 0;
volatile bool batchFull = false;

// D2 interrupt
// ISR only stores timestamp.

void spadEvent()
{
  if (batchFull)
  {
    return;
  }

  timestamps[captureIndex] = TCNT1;

  captureIndex++;

  if (captureIndex >= BATCH_SIZE)
  {
    batchFull = true;
  }
}


// Setup

void setup()
{
  // UART:
  // COM5 + physical D1/TX
  Serial.begin(115200);

  pinMode(SPAD_PIN, INPUT);

  // Timer1
  //
  // Clock = 16 MHz
  // Prescaler = 1
  //
  // Resolution = 62.5 ns


  TCCR1A = 0;
  TCCR1B = 0;

  TCNT1 = 0;

  TCCR1B = _BV(CS10);
}


// Main loop

void loop()
{

  // Prepare new independent batch

  noInterrupts();

  captureIndex = 0;
  batchFull = false;

  interrupts();


  // Clear any pending D2 interrupt.
  EIFR = _BV(INTF0);


  // Start acquisition

  attachInterrupt(
    digitalPinToInterrupt(SPAD_PIN),
    spadEvent,
    RISING
  );


  // Wait for 128 SPAD events


  while (!batchFull)
  {
    // ISR collects timestamps.
  }


  // Stop acquisition


  detachInterrupt(
    digitalPinToInterrupt(SPAD_PIN)
  );


  // Copy timestamps

  uint16_t localTime[BATCH_SIZE];

  noInterrupts();

  for (uint8_t i = 0; i < BATCH_SIZE; i++)
  {
    localTime[i] = timestamps[i];
  }

  interrupts();


  // Calculate 127 intervals


  uint16_t dt[BATCH_SIZE - 1];

  for (uint8_t i = 0; i < BATCH_SIZE - 1; i++)
  {
    dt[i] =
      (uint16_t)(localTime[i + 1] - localTime[i]);
  }


  // Generate raw bits
  //
  // dt[0] vs dt[1]
  // dt[2] vs dt[3]
  // dt[4] vs dt[5]
  // ...


  uint8_t rawBits[63];

  uint8_t rawBitCount = 0;


  for (uint8_t i = 0; i < 126; i += 2)
  {
    uint16_t dt1 = dt[i];
    uint16_t dt2 = dt[i + 1];


    if (dt1 > dt2)
    {
      rawBits[rawBitCount] = 1;

      rawBitCount++;
    }


    else if (dt1 < dt2)
    {
      rawBits[rawBitCount] = 0;

      rawBitCount++;
    }


    else
    {
      // Exact tie:
      // discard
    }
  }


  // Pack raw bits into bytes
  //
  // First generated raw bit becomes bit 7.
  //
  // Example:
  //
  // raw bits:
  // 10110110
  //
  // binary byte:
  // 0xB6
  //
  // Only COMPLETE bytes are sent.
  // Remaining 1-7 bits are discarded.


  uint8_t currentByte = 0;
  uint8_t currentBitCount = 0;


  for (uint8_t i = 0; i < rawBitCount; i++)
  {
    currentByte =
      (currentByte << 1) | rawBits[i];

    currentBitCount++;


    if (currentBitCount == 8)
    {
  
      // PURE BINARY OUTPUT
      //
      // Goes simultaneously to:
      //
      // COM5
      // and
      // RedBoard D1/TX -> FPGA
      // NO text is added.
   

      Serial.write(currentByte);


      currentByte = 0;
      currentBitCount = 0;
    }
  }


  // Wait until all UART bytes have left D1/TX.
  Serial.flush();


  // Keep same batch separation as previous validated setup.
  delay(1);
}