"""Generate translations/en.json from strings.json.

Custom integrations get no reference resolution at runtime, so every
``[%key:...%]`` is replaced with the text from Home Assistant or strings.json.
Run with ``--check`` to fail when en.json is out of date.
"""

import json
from pathlib import Path
import re
import sys
from typing import Any

import homeassistant

INTEGRATION = Path(__file__).parents[1] / "custom_components" / "local_vision"
DOMAIN = INTEGRATION.name
CORE = Path(homeassistant.__file__).parent
REFERENCE = re.compile(r"\[%key:([a-z0-9_:]+)%\]")


def _lookup(strings: dict[str, Any], key: str) -> str:
    parts = key.split("::")
    if parts[0] == "common":
        source = json.loads((CORE / "strings.json").read_text())
        path = parts
    elif parts[:2] == ["component", DOMAIN]:
        source, path = strings, parts[2:]
    elif parts[0] == "component":
        en = CORE / "components" / parts[1] / "translations" / "en.json"
        source, path = json.loads(en.read_text()), parts[2:]
    else:
        raise KeyError(key)
    value: Any = source
    for part in path:
        value = value[part]
    return _resolve(strings, value)


def _resolve(strings: dict[str, Any], value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _resolve(strings, item) for key, item in value.items()}
    if isinstance(value, str):
        return REFERENCE.sub(lambda match: _lookup(strings, match[1]), value)
    return value


def main() -> int:
    """Write or check en.json."""
    strings = json.loads((INTEGRATION / "strings.json").read_text())
    generated = json.dumps(_resolve(strings, strings), indent=2, ensure_ascii=False)
    target = INTEGRATION / "translations" / "en.json"
    if "--check" in sys.argv:
        if target.read_text() != generated + "\n":
            print(f"{target} is out of date, run script/translations.py")
            return 1
        return 0
    target.write_text(generated + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
