<p align="center">
  <img src="custom_components/local_vision/brand/icon@2x.png" alt="Local vision" width="128">
</p>

# Local vision

Is the garage door open? Is the car in the garage? Point a camera at it, show Local vision one frame of each state, and you get a binary sensor for it. No cloud, no account, no training: recognition runs on your Home Assistant machine with [tarsius](https://github.com/RaHehl/tarsius), and no image leaves your network.

A personal project, not an official Home Assistant integration.

Two helper types:

- **State of an area**: the simple one. A binary sensor for a part of the camera view: a garage door open or closed, a car parked or not, a gate, a mailbox flag. You teach it with frames from the camera.
- **Meter reading (experimental)**: a sensor with the reading of a water, gas or electricity meter, ready for the Energy dashboard. It reads the counter wheels and, on analog meters, the pointer dials (×0.1 down to ×0.0001) together with them. Home Assistant finds the counter and the dials in the camera frame for you.

## Installation

Requires Home Assistant 2026.10 or newer.

Install **Local vision** from the Marketplace (HACS). Until it is listed there, add `https://github.com/RaHehl/local-vision` as a custom repository of type integration. Restart Home Assistant afterwards.

Manual installation: copy `custom_components/local_vision` into the `custom_components` folder of your configuration and restart.

Then go to **Settings** > **Devices & services** > **Helpers** > **Create helper** > **Local vision**.

## State of an area: garage door, car, gate

1. Pick the camera, the kind of sensor (garage door, door, occupancy, opening, window) and a name.
2. Set the region in percent of the frame, for example around the garage door. The next step shows it outlined on the current frame.
3. Open the helper's options and teach the current frame with the state it shows: door open, door closed. Teach at least one frame of each state. More frames in different light, such as day, night and with the lights on, make it more reliable.

The sensor is unknown until both states are taught and while it is unsure. It is added to the camera's device. Taught frames can be removed in the options, for example after the camera was moved.

## Meter reading (experimental)

This helper type is experimental: check its value against your meter for the first days, and tell us how it does (see below).

1. Pick the camera, what the meter counts (water, gas, energy) and a name. A gas meter that counts kWh is set up as energy.
2. Choose the unit printed on the meter and, optionally, the maximum increase per hour. Left empty it is 4 m³ for water, 6 m³ for gas and 50 kWh for electricity, or the same in the chosen unit.
3. Choose **Find the counter automatically**: Home Assistant looks for the counter window and the pointer dials in the current frame and fills in the forms, including the rotation. If it finds nothing, you enter the regions yourself.
4. Check the window with the counter wheels: its edges in percent, the rotation that levels the digits, the number of wheels, how many of them count after the decimal point, and the number of pointer dials.
5. Check the region of each dial, most significant first, and whether it turns counterclockwise.
6. Check the regions and the value read from the current frame.
7. Confirm the value the meter shows now, or correct it. Every later reading is checked against it.

Pointer dials are read together with the wheels: on a water meter with a ×0.1, ×0.01, ×0.001 and ×0.0001 dial the sensor shows tenths of a litre, and the dials also tell which way an upper wheel near its carry goes.

The sensor has the state class `total` and the device class you chose, so it can be added to the Energy dashboard. It only takes readings that pass these checks:

- every wheel and dial is read clearly, without a capture fault such as a glare spot, a covered wheel or blur; how each wheel and dial looks is learnt at setup and follows slow changes of the light, so a darker night or fogged glass is no fault,
- the wheels and dials agree with each other, and no wheel stands so close between two digits that either could be right,
- the value is not lower than the last accepted one; a lower value is taken once three readings in a row agree on it, for example after a new meter, unless the drop is whole digits of an upper wheel, which looks like a misread; when that lasts six hours, a repair asks you to check the meter and enter its value,
- the value rose no faster than the maximum increase per hour since the last accepted reading, also after a long gap.

Near a carry, or when the reader sees a wheel slightly off, the wheels allow two values, such as 619.9 and 609.9; the sensor takes the one that fits the last accepted value. A reading slightly below the last value, as a rolling last wheel or dial jitters, keeps the value, also after a long gap. A rejected reading leaves the value as it is. When the counter wraps from its highest values to zero, the sensor counts on; a jump from the middle of its range to near zero is a drop, not a wrap. The last accepted value, when it was read, and what the checks remember, such as lower readings waiting for agreement, are stored and survive restarts.

Because the state class is `total`, a value that goes down counts as a negative change in the statistics: a wrong reading that was too high is taken back by the correction instead of starting a new meter cycle.

Two diagnostic sensors show what happened: **reading status** (accepted, or why the reading was rejected, with the value read as attribute) and **reading confidence** (disabled by default). If a wrong value was accepted, or the meter was replaced, set the right value in the helper's options. If a meter has no confirmed value, for example after its stored data was lost, it takes no readings and a repair asks for the value; enter it right in the repair.

To change the unit, the maximum increase or the regions, for example after the camera was moved, reconfigure the helper. The value is kept, unless the counter reads a different number afterwards, such as with another number of wheels; then the flow asks for it again.

### Finding the counter

The automatic setup is part of the experimental meter type. A small model marks the counter window and the dials; it can miss the meter or mark something else, such as a printed serial number, and it does not count the wheels. Check every region before you continue. On a Home Assistant Green it can take a few seconds. You can always enter the regions yourself instead.

## Updates

Both helper types read a frame every 5 minutes. To read more or less often, disable polling in the helper's system options and call `homeassistant.update_entity` from an automation, for example when a door opens.

## Limitations

- The camera must show the area or the meter every time it is read. Night frames need enough light or infrared.
- The region of a state sensor can't be changed later; add the helper again.
- A state sensor knows two states. A wide region with clutter that moves around makes it less reliable.
- Meter reading works on mechanical counter wheels and pointer dials, not on LCD displays. The camera should look straight at the meter with sharp digits.

## Privacy

Everything runs locally. Frames are fetched from the camera entity, compared on the Home Assistant machine and discarded. Taught frames of state sensors are stored as JPEG files in `config/local_vision/<entry id>/`, so they are part of Home Assistant backups, and are deleted with the helper. Meter helpers store the last accepted value, when it was read, what the checks remember and grey-level statistics of each wheel and dial, no images.

## Help make it better

Local vision gets better with every camera and meter it meets. You can help without writing code:

- **Tell us how it works for you**: open an issue with the result of a state helper or a meter reading, good or bad. There are short forms for both.
- **Share frames of your meter**: crops of the counter and dials help train the meter models. Crop to the counter, blur or cover serial numbers and anything that identifies you, and share them under CC0 with the meter sample form.
- **Try the experimental parts** on other meter types: gas meters, electricity meters, meters with red wheels or many dials.
- **Translations and code**: pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup and the tests.

## Development

```sh
uv sync --group test
uv run pytest -n 2
uvx prek run --all-files
```

`custom_components/local_vision/translations/en.json` is generated from `strings.json` with `uv run python script/translations.py`.

## License

Apache-2.0
