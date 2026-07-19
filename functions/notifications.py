from firebase_admin import messaging


def send_fcm_data_message(driver: dict, order_data: dict) -> bool:
    try:
        message = messaging.Message(
            token=driver["fcmToken"],
            data={
                "type": "NEW_ORDER",
                "orderId": order_data["orderId"],
                "pickupAddress": order_data["pickup"]["address"],
                "dropoffAddress": order_data["dropoff"]["address"],
                "createdAt": order_data["createdAt"].isoformat(),
            },
        )
        messaging.send(message)
        return True
    except Exception as e:
        print(f"[send_fcm_data_message] failed for driver {driver.get('driverId')}: {e}")
        return False
