from firebase_functions import firestore_fn, https_fn
from firebase_functions.options import set_global_options
from firebase_admin import firestore, initialize_app

import dispatch_logic
import earnings
import notifications
import transaction_handlers
from config import RETRY_DELAY_SECONDS

set_global_options(max_instances=10)

initialize_app()


@firestore_fn.on_document_created(document="orders/{orderId}")
def on_order_created(event: firestore_fn.Event) -> None:
    try:
        order_id = event.params["orderId"]
        order_data = event.data.to_dict()
        order_data.setdefault("orderId", order_id)

        drivers = dispatch_logic.get_nearest_online_drivers(order_data["pickup"]["location"])
        if not drivers:
            dispatch_logic.mark_order_expired(order_id)
            return

        for driver in drivers:
            notifications.send_fcm_data_message(driver, order_data)

        dispatch_logic.schedule_retry(order_id, attempt=2, delay=RETRY_DELAY_SECONDS)
    except Exception as e:
        print(f"[on_order_created] error: {e}")


@firestore_fn.on_document_updated(document="orders/{orderId}", timeout_sec=120)
def on_retry_due(event: firestore_fn.Event) -> None:
    try:
        before_next_retry = event.data.before.to_dict().get("nextRetryAt")
        after_next_retry = event.data.after.to_dict().get("nextRetryAt")
        if after_next_retry is None or before_next_retry == after_next_retry:
            return

        order_id = event.params["orderId"]
        dispatch_logic.execute_scheduled_retry(order_id)
    except Exception as e:
        print(f"[on_retry_due] error: {e}")


@firestore_fn.on_document_updated(document="orders/{orderId}")
def on_delivery_completed(event: firestore_fn.Event) -> None:
    try:
        before_status = event.data.before.to_dict()["status"]
        after_status = event.data.after.to_dict()["status"]
        if after_status != "delivered" or before_status == "delivered":
            return

        order_data = event.data.after.to_dict()
        zone_earnings = earnings.get_zone_earnings(order_data["zone"])
        driver_id = order_data["assignedDriverId"]

        db = firestore.client()
        driver_ref = db.collection("drivers").document(driver_id)
        driver_ref.update({
            "totalEarnings": firestore.Increment(zone_earnings),
            "pendingPayout": firestore.Increment(zone_earnings),
            "currentOrderId": None,
        })
    except Exception as e:
        print(f"[on_delivery_completed] error: {e}")


@https_fn.on_call()
def accept_order(req: https_fn.CallableRequest) -> dict:
    order_id = req.data.get("orderId")
    driver_id = req.data.get("driverId")
    if not isinstance(order_id, str) or not order_id or not isinstance(driver_id, str) or not driver_id:
        return {"success": False, "error": "Missing orderId or driverId"}

    try:
        db = firestore.client()
        order_ref = db.collection("orders").document(order_id)
        transaction = db.transaction()
        transaction_handlers.accept_order_transaction(transaction, order_ref, driver_id)
        return {"success": True}
    except Exception as e:
        return transaction_handlers.handle_accept_failure(e)
