# Contributing to Local vision

Thanks for helping. Every report, frame and fix counts.

## Reports

Open an issue with one of the forms:

- **State helper result**: what you watch (a garage door, a car), how many frames you taught, and how often the sensor was right, unsure or wrong.
- **Meter reading**: your meter type, the unit, whether the automatic setup found the counter and the dials, and how the sensor's value compares with the meter over a few days. Attach the diagnostics (helper page > three dots > Download diagnostics) or the reading status history if you can.
- **Bug** and **feature request** for everything else.

## Meter samples

Frames of real meters help train the wheel, dial and finder models. Use the **Meter sample** form.

1. Crop each frame to the counter window and the dials, with a little room around them.
2. Blur or cover serial numbers, addresses, names and anything else that identifies you or your home.
3. Write down the value the meter shows in each frame, if you can.
4. Confirm in the form that you share the images under [CC0](https://creativecommons.org/publicdomain/zero/1.0/): anyone may use them for anything, also for training models.

## Code and translations

```sh
uv sync --group test
uv run pytest -n 2
uvx prek run --all-files
```

- The tests run with `pytest-homeassistant-custom-component`; keep them at full coverage.
- Texts live in `custom_components/local_vision/strings.json`; regenerate `translations/en.json` with `uv run python script/translations.py`.
- Recognition itself lives in [tarsius](https://github.com/RaHehl/tarsius); changes to reading or the models go there.

Small pull requests that do one thing are easiest to review.
