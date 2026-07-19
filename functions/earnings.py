ZONE_FEES: dict[str, int] = {
    "kigali-central": 1500,
    "kigali-north": 1800,
    "kigali-south": 1800,
    "kigali-east": 2000,
    "kigali-west": 2000,
}
DEFAULT_ZONE_FEE = 1500


def get_zone_earnings(zone: str) -> int:
    return ZONE_FEES.get(zone, DEFAULT_ZONE_FEE)
