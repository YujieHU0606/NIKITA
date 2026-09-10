const byte SPAD_PIN = 2;

volatile unsigned long pulseCount = 0;
volatile bool counting = false;

const unsigned long MEASURE_TIME_MS = 60000UL;

unsigned long startTime = 0;
bool finished = false;


void pulseISR()
{
  if (counting)
  {
    pulseCount++;
  }
}


void setup()
{
  Serial.begin(115200);

  pinMode(SPAD_PIN, INPUT);

  attachInterrupt(
    digitalPinToInterrupt(SPAD_PIN),
    pulseISR,
    RISING
  );

  noInterrupts();

  pulseCount = 0;
  startTime = millis();
  finished = false;
  counting = true;

  interrupts();

  Serial.println();
  Serial.println("D2 DCR measurement started.");
  Serial.println("Measuring for 60 seconds...");
}


void loop()
{
  if (
    !finished
    && millis() - startTime >= MEASURE_TIME_MS
  )
  {
    noInterrupts();

    counting = false;
    unsigned long finalCount = pulseCount;

    interrupts();

    unsigned long elapsedTime =
      millis() - startTime;

    float elapsedSeconds =
      elapsedTime / 1000.0;

    float dcr =
      finalCount / elapsedSeconds;

    finished = true;

    Serial.println();
    Serial.println("60 s DCR result");

    Serial.print("Events : ");
    Serial.println(finalCount);

    Serial.print("Time   : ");
    Serial.print(elapsedSeconds, 3);
    Serial.println(" s");

    Serial.print("DCR    : ");
    Serial.print(dcr, 2);
    Serial.println(" cps");

    Serial.println();
    Serial.println("Measurement stopped.");
    Serial.println("Turn Keithley OFF.");
  }
}