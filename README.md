<p align="center">
  <img src="custom_components/local_vision/brand/icon@2x.png" alt="Local vision" width="128">
</p>

# Local vision

Is the garage door open? Is the car in the garage? Point a camera at it, show Local vision a frame of each state, and you get a sensor for it. Everything runs on your Home Assistant machine with [tarsius](https://github.com/RaHehl/tarsius): no cloud, no account, no image leaves your network.

A personal project, not an official Home Assistant integration.

Two helper types:

- **State of an area:** a binary sensor for part of the camera view, such as a garage door, a parked car, a gate or a mailbox flag.
- **Meter reading (experimental):** the reading of a water, gas or electricity meter for the Energy dashboard, from counter wheels and pointer dials.

## Installation

Requires Home Assistant 2026.10 or newer.

Install **Local vision** from the Marketplace (HACS):

[![Open Local vision in the Marketplace (HACS) of your Home Assistant.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=RaHehl&repository=local-vision&category=integration)

Restart Home Assistant.

Then go to **Settings** > **Devices & services** > **Helpers** > **Create helper** > **Local vision**.

## State of an area

https://github.com/user-attachments/assets/cc40e55a-6d74-43ce-8fa2-ac992730097c

1. Pick the camera, the kind of sensor (garage door, door, occupancy, opening, window) and a name.
2. Set the region around the garage door, the parking spot or the gate. The next step shows it on the current frame.
3. In the helper's options, teach the current frame with the state it shows. Teach each state at least once; frames in different light (day, night, lights on) make it more reliable.

The sensor stays unknown until both states are taught, and while it is unsure.

## Meter reading (experimental)

https://github.com/user-attachments/assets/c8139dda-6c49-41ca-b806-787866d57968

Check the value against your meter for the first days, and tell us how it does.

1. Pick the camera, what the meter counts and the unit printed on it.
2. Let Local vision **suggest the counter and the dials**, or enter the regions yourself. Check every region.
3. Enter the number of wheels and how many count after the decimal point.
4. Check each dial: the suggested order is a guess, and you set whether a dial turns counterclockwise.
5. Confirm the value the meter shows now. Every later reading is checked against it.

The sensor only takes a reading when:

- every wheel and dial is read clearly (no glare spot, no covered wheel),
- the wheels and dials agree with each other,
- the value did not go down, and did not rise faster than the maximum per hour.

If readings keep disagreeing with the stored value for six hours, a repair asks you to enter the value from the meter. If a wrong value was accepted, or the meter was replaced, correct it in the helper's options. After the camera moved, reconfigure the helper.

The sensor has the state class `total`, so it works in the Energy dashboard and a correction is not counted as consumption. [docs/meter-reading.md](docs/meter-reading.md) explains the checks in detail.

### Suggested regions

The suggestion works best on a straight-on view of one meter. On varied photos it found the counter about half of the time; it can miss vertically mounted counters or pick a serial number instead. It takes a few seconds on a Home Assistant Green.

## Good to know

- Both helper types read a frame every 5 minutes. For other intervals, disable polling in the helper's system options and call `homeassistant.update_entity` from an automation.
- The camera must show the area or the meter on every read. Night frames need light or infrared.
- Meter reading works on mechanical wheels and pointer dials, not on LCD displays.
- Taught frames are stored in `config/local_vision/<entry id>/` and are part of your backups. Meter helpers store values and statistics, no images.

## Help make it better

Local vision gets better with every camera and meter it meets. You can help without writing code:

- **Report how it works:** open an issue with the result of a state helper or a meter, good or bad.
- **Share meter frames:** crops of the counter and dials help train the models. Cover serial numbers and share them under CC0 with the meter sample form.
- **Try other meters:** gas, electricity, red wheels, many dials, and tell us whether the suggested regions fit.
- **Translations and code:** pull requests are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Development

```sh
uv sync --group test
uv run pytest -n 2
uvx prek run --all-files
```

`custom_components/local_vision/translations/en.json` is generated from `strings.json` with `uv run python script/translations.py`.

## License

Apache-2.0
