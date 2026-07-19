from datetime import datetime
from typing import Literal, TypedDict

from google.cloud.firestore_v1 import GeoPoint

OrderStatus = Literal["pending", "assigned", "picked_up", "delivered", "expired"]


class Driver(TypedDict):
    driverId: str
    name: str
    phone: str
    isOnline: bool
    location: GeoPoint
    geohash: str
    currentOrderId: str | None
    fcmToken: str
    totalEarnings: int
    pendingPayout: int
    createdAt: datetime


class OrderParty(TypedDict):
    name: str
    address: str
    location: GeoPoint
    phone: str


class Order(TypedDict):
    orderId: str
    pickup: OrderParty
    dropoff: OrderParty
    zone: str
    status: OrderStatus
    assignedDriverId: str | None
    assignedAt: datetime | None
    dispatchAttempt: int
    createdAt: datetime
    expiresAt: datetime | None
    nextRetryAt: datetime | None
