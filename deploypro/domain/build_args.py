"""Build arguments: `NAME=value` lines passed to `docker build --build-arg`.

For a Dockerfile's ARG switches — BalanceVid's `WITH_TEXT=1` — and nothing
else. A build argument is written into the image's metadata and printed by
`docker history`, so it is no place for a secret: those are environment
variables, which reach a build only as a BuildKit secret mount. A name that
looks like a secret is refused here, with the way to do it properly.
"""

from __future__ import annotations

import re

from deploypro.domain.errors import InvalidRequest

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
MAX_ARGS = 50
MAX_VALUE = 1000

#: Parts of a name that mean "this is a credential". Whole words of the
#: name, so `WITH_TEXT` and `KEYFRAMES` pass and `API_KEY` does not.
SECRET_WORDS = frozenset(
    {"PASSWORD", "PASSWD", "SECRET", "TOKEN", "CREDENTIAL", "CREDENTIALS", "PRIVATE"}
)
SECRET_PAIRS = ("API_KEY", "ACCESS_KEY", "SECRET_KEY", "AUTH_KEY", "MASTER_KEY")


def parse(text: str) -> dict[str, str]:
    """Checked `{NAME: value}` from the owner's lines, in their order.

    Blank lines and `#` comments are skipped. A repeated name keeps its last
    value, as `docker build` would.
    """
    args: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        # Trimmed both sides: `WITH_TEXT = 1` means 1, not " 1".
        name, value = name.strip(), value.strip()
        if not sep:
            raise InvalidRequest(
                f"Line {number}: write it as NAME=value, like WITH_TEXT=1."
            )
        if not NAME.match(name):
            raise InvalidRequest(
                f"Line {number}: “{name}” is not a build argument name. Use letters, "
                "digits and _, not starting with a digit."
            )
        if looks_secret(name):
            raise InvalidRequest(
                f"{name} looks like a secret, and build arguments are visible in "
                "the image. Add it under Environment instead, where it never goes "
                "into the image."
            )
        if len(value) > MAX_VALUE:
            raise InvalidRequest(
                f"Line {number}: the value is longer than {MAX_VALUE} characters."
            )
        args[name] = value
    if len(args) > MAX_ARGS:
        raise InvalidRequest(f"At most {MAX_ARGS} build arguments.")
    return args


def looks_secret(name: str) -> bool:
    upper = name.upper()
    if any(pair in upper for pair in SECRET_PAIRS):
        return True
    return any(word in SECRET_WORDS for word in upper.split("_"))


def to_text(args: dict[str, str]) -> str:
    """The stored form: one NAME=value per line."""
    return "".join(f"{name}={value}\n" for name, value in args.items())
