# Meter reading: details

The README shows how to set up a meter. This page explains how readings are checked.

Pointer dials are read together with the wheels: on a water meter with a ×0.1, ×0.01, ×0.001 and ×0.0001 dial the sensor shows tenths of a litre, and the dials also tell which way an upper wheel near its carry goes.

The sensor has the state class `total` and the device class you chose, so it can be added to the Energy dashboard. It only takes readings that pass these checks:

- every wheel and dial is read clearly, without a capture fault such as a glare spot, a covered wheel or blur; how each wheel and dial looks is learnt at setup and follows slow changes of the light, so a darker night or fogged glass is no fault,
- the wheels and dials agree with each other, and no wheel stands so close between two digits that either could be right,
- the value is not lower than the last accepted one; a lower value is taken once three readings in a row agree on it, for example after a new meter, unless the drop is whole digits of an upper wheel, which looks like a misread,
- the value rose no faster than the maximum increase per hour since the last accepted reading, also after a long gap; an unsure reading that rises by a whole digit waits for a second one that agrees.

When readings stay a whole digit lower, or keep rising too fast, for six hours, a repair asks you to look at the meter and enter its value.

Near a carry, or when the reader sees a wheel slightly off, the wheels allow two values, such as 619.9 and 609.9; the sensor takes the one that fits the last accepted value. A reading slightly below the last value, as a rolling last wheel or dial jitters, keeps the value, also after a long gap. A rejected reading leaves the value as it is. When the counter wraps from its highest values to zero, the sensor counts on; a jump from the middle of its range to near zero is a drop, not a wrap. The last accepted value, when it was read, and what the checks remember, such as lower readings waiting for agreement, are stored and survive restarts.

Because the state class is `total`, a value that goes down counts as a negative change in the statistics: a wrong reading that was too high is taken back by the correction instead of starting a new meter cycle.

Two diagnostic sensors show what happened: **reading status** (accepted, or why the reading was rejected, with the value read as attribute) and **reading confidence** (disabled by default). If a wrong value was accepted, or the meter was replaced, set the right value in the helper's options. If a meter has no confirmed value, for example after its stored data was lost, it takes no readings and a repair asks for the value; enter it right in the repair.

To change the unit, the maximum increase or the regions, for example after the camera was moved, reconfigure the helper. The value is kept, unless the counter reads a different number afterwards, such as with another number of wheels; then the flow asks for it again.
