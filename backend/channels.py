"""Channel metadata and timestamp offsets; all channels use the same evaluator."""
from dataclasses import asdict, dataclass
from datetime import datetime


@dataclass(frozen=True)
class ChannelProfile:
    timestamp_format: str = "opaque_or_missing"
    audio_link_supported: bool = True
    evaluation_source: str = "transcript_only"
    excluded_observations: tuple[str, ...] = ("intonation", "diction", "audio_quality")


CHANNEL_PROFILES = {
    "agent chat": ChannelProfile(timestamp_format="MM:SS_or_missing"),
    "voice call": ChannelProfile(timestamp_format="ISO8601_with_timezone_or_missing"),
    "email": ChannelProfile(),
    "whatsapp": ChannelProfile(),
}
DEFAULT_PROFILE = ChannelProfile()


def channel_profile(channel: str) -> ChannelProfile:
    return CHANNEL_PROFILES.get(channel.strip().casefold(), DEFAULT_PROFILE)


def channel_context(channel: str) -> dict:
    return asdict(channel_profile(channel))


def _iso_time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None and result.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def timestamp_offsets(channel: str, timestamps: list[str]):
    """Return offsets/warnings without ever rewriting a supplied timestamp string."""
    offsets = [None] * len(timestamps)
    warnings = []
    if channel_profile(channel).timestamp_format != "ISO8601_with_timezone_or_missing":
        return offsets, warnings
    parsed = [_iso_time(value) for value in timestamps]
    origin = parsed[0] if parsed else None
    for index, current in enumerate(parsed, start=1):
        if current is None:
            warnings.append({"message_id": f"m{index}", "code": "INVALID_ISO_TIMESTAMP"})
        if origin is not None and current is not None:
            offsets[index - 1] = (current - origin).total_seconds()
        elif index > 1 and origin is None and current is not None:
            warnings.append({"message_id": f"m{index}", "code": "INVALID_TIME_ORIGIN"})
    return offsets, warnings
