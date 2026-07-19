import time
from datetime import datetime, timedelta, timezone

import geohash2
from firebase_admin import firestore
from google.cloud.firestore_v1 import GeoPoint
from google.cloud.firestore_v1.base_query import FieldFilter

import notifications
from config import (
    DRIVER_SEARCH_LIMIT,
    GEOHASH_PRECISION,
    MAX_DISPATCH_ATTEMPTS,
    MIN_GEOHASH_PREFIX_LENGTH,
    RETRY_DELAY_SECONDS,
)

_GEOHASH_PREFIX_UPPER_BOUND = ""


def _get_db():
    return firestore.client()


def _shared_prefix_length(a: str, b: str) -> int:
    length = 0
    for char_a, char_b in zip(a, b):
        if char_a != char_b:
            break
        length += 1
    return length


def get_nearest_online_drivers(location: GeoPoint, limit: int = DRIVER_SEARCH_LIMIT) -> list[dict]:
    db = _get_db()
    target_hash = geohash2.encode(location.latitude, location.longitude, precision=GEOHASH_PRECISION)

    seen_ids: set[str] = set()
    candidates: list[dict] = []

    for prefix_length in range(GEOHASH_PRECISION, MIN_GEOHASH_PREFIX_LENGTH - 1, -1):
        prefix = target_hash[:prefix_length]
        query = (
            db.collection("drivers")
            .where(filter=FieldFilter("isOnline", "==", True))
            .where(filter=FieldFilter("geohash", ">=", prefix))
            .where(filter=FieldFilter("geohash", "<=", prefix + _GEOHASH_PREFIX_UPPER_BOUND))
        )
        for doc in query.stream():
            if doc.id in seen_ids:
                continue
            seen_ids.add(doc.id)
            driver = doc.to_dict()
            driver.setdefault("driverId", doc.id)
            candidates.append(driver)

        if len(candidates) >= limit:
            break

    candidates.sort(key=lambda driver: -_shared_prefix_length(driver.get("geohash", ""), target_hash))

    return candidates[:limit]


def mark_order_expired(order_id: str) -> None:
    order_ref = _get_db().collection("orders").document(order_id)
    order_ref.update(
        {
            "status": "expired",
            "expiresAt": firestore.SERVER_TIMESTAMP,
        }
    )


def schedule_retry(order_id: str, attempt: int, delay: int) -> None:
    if attempt > MAX_DISPATCH_ATTEMPTS:
        return

    order_ref = _get_db().collection("orders").document(order_id)
    next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
    order_ref.update(
        {
            "dispatchAttempt": attempt,
            "nextRetryAt": next_retry_at,
        }
    )


def execute_scheduled_retry(order_id: str, give_up_after: int = RETRY_DELAY_SECONDS) -> None:
    order_ref = _get_db().collection("orders").document(order_id)

    order_data = order_ref.get().to_dict()
    if order_data is None:
        return

    next_retry_at = order_data.get("nextRetryAt")
    if next_retry_at is not None:
        remaining = (next_retry_at - datetime.now(timezone.utc)).total_seconds()
        if remaining > 0:
            time.sleep(remaining)
        order_data = order_ref.get().to_dict()

    if order_data is None or order_data.get("status") != "pending":
        return

    order_ref.update({"nextRetryAt": None})

    drivers = get_nearest_online_drivers(order_data["pickup"]["location"])
    if not drivers:
        mark_order_expired(order_id)
        return

    for driver in drivers:
        notifications.send_fcm_data_message(driver, order_data)

    time.sleep(give_up_after)
    final_snapshot = order_ref.get().to_dict()
    if final_snapshot is not None and final_snapshot.get("status") == "pending":
        mark_order_expired(order_id)
