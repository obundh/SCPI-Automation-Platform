from __future__ import annotations

from .models import IdentityParseError, InstrumentIdentity


def _clean_field(value: str) -> str:
    return value.strip().strip('"').strip("'").strip()


def parse_idn_response(raw_response: str) -> InstrumentIdentity:
    """Parse the usual IEEE 488.2 four-field *IDN? response.

    The last field is allowed to contain commas because some instruments put
    multiple firmware components there.
    """

    raw = raw_response.replace("\x00", "").strip()
    if not raw:
        raise IdentityParseError("IDN 응답이 비어 있습니다.")

    parts = [_clean_field(part) for part in raw.split(",", 3)]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise IdentityParseError(
            "IDN 응답에서 제조사와 모델을 찾을 수 없습니다. "
            "일반 형식은 제조사,모델,시리얼,펌웨어입니다."
        )

    parts.extend([""] * (4 - len(parts)))
    return InstrumentIdentity(
        raw=raw,
        manufacturer=parts[0],
        model=parts[1],
        serial=parts[2],
        firmware=parts[3],
    )

