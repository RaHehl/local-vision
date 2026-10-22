<p align="center">
  <img src="custom_components/local_vision/brand/icon@2x.png" alt="Local vision" width="128">
</p>

# Local vision

Local image recognition for Home Assistant cameras, as helpers. Recognition runs on the Home Assistant machine with [tarsius](https://github.com/RaHehl/tarsius). No cloud service, no account, no image leaves your network.

Two helper types:

- **State of an area**: a binary sensor for a part of the camera view, such as a garage door that is open or closed, a car in the driveway or a mailbox flag. You teach it with frames from the camera, there is no training.
- **Meter reading**: a sensor with the reading of a water, gas or electricity meter with counter wheels and optional pointer dials, ready for the Energy dashboard.

## Installation

Requires Home Assistant 2026.10 or newer.

Install **Local vision** from the Marketplace (HACS). Until it is listed there, add `https://github.com/RaHehl/local-vision` as a custom repository of type integration. Restart Home Assistant afterwards.

Manual installation: copy `custom_components/local_vision` into the `custom_components` folder of your configuration and restart.

Then go to **Settings** > **Devices & services** > **Helpers** > **Create helper** > **Local vision**.

## State of an area

1. Pick the camera, the kind of sensor (door, garage door, occupancy, opening, window) and a name.
2. Set the region in percent of the frame. The next step shows it outlined on the current frame.
3. Open the helper's options and teach the current frame with the state it shows. Teach at least one frame of each state. More frames in different light, such as day, night and with the lights on, make it more reliable.

The sensor is unknown until both states are taught and while it is unsure. It is added to the camera's device. Taught frames can be removed in the options, for example after the camera was moved.

## Meter reading

1. Pick the camera, what the meter counts (water, gas, energy) and a name.
2. Choose the unit printed on the meter and, optionally, the maximum increase per hour.
3. Enter the regions or let Home Assistant suggest them (see below).
4. Set the window with the counter wheels: its edges in percent, the rotation that levels the digits, the number of wheels, how many of them count after the decimal point, and the number of pointer dials.
5. Set the region of each dial, most significant first, and whether it turns counterclockwise.
6. Check the regions and the value read from the current frame, then create the sensor.

The sensor has the state class `total_increasing` and the device class you chose, so it can be added to the Energy dashboard. It only takes readings that pass these checks:

- every wheel and dial is read clearly, without a capture fault such as glare or blur,
- the wheels and dials agree with each other,
- the value is not lower than the last accepted one,
- the value rose no faster than the maximum increase per hour.

A rejected reading leaves the value as it is. The sensor never goes down on its own. The last accepted value and the state of these checks are stored and survive restarts.

Two diagnostic sensors show what happened: **reading status** (accepted, or why the reading was rejected, with the value read as attribute) and **reading confidence** (disabled by default). If a wrong value was accepted, or the meter was replaced, set the right value in the helper's options.

### Suggest regions (alpha)

The flow offers **Suggest regions (alpha)**: a small model looks for the counter window and the dials in the current frame and fills in the forms, including the rotation. It is an early version. It can miss the meter or mark something else, such as a printed serial number, and it does not count the wheels. Check every region before you continue. It is never required, you can always enter the regions yourself.

## Updates

Both helper types read a frame every 5 minutes. To read more or less often, disable polling in the helper's system options and call `homeassistant.update_entity` from an automation, for example when a door opens.

## Limitations

- The camera must show the area or the meter every time it is read. Night frames need enough light or infrared.
- The region of a state sensor can't be changed later; add the helper again.
- A state sensor knows two states. A wide region with clutter that moves around makes it less reliable.
- Meter reading works on mechanical counter wheels and pointer dials, not on LCD displays. The camera should look straight at the meter with sharp digits.
- The meter regions can't be changed later; add the helper again.

## Privacy

Everything runs locally. Frames are fetched from the camera entity, compared on the Home Assistant machine and discarded. Taught frames of state sensors are stored in `config/local_vision/<entry id>/` and deleted with the helper. Meter helpers store only the last value.

## Development

```sh
uv sync --group test
uv run pytest -n 2
uvx prek run --all-files
```

`custom_components/local_vision/translations/en.json` is generated from `strings.json` with `uv run python script/translations.py`.

## License

Apache-2.0
