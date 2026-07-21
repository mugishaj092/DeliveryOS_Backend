from firebase_admin import firestore
from google.cloud.firestore_v1 import Transaction
from google.cloud.firestore_v1.document import DocumentReference


@firestore.transactional
def accept_order_transaction(transaction: Transaction, order_ref: DocumentReference, driver_id: str) -> None:
    order_snapshot = order_ref.get(transaction=transaction)
    order = order_snapshot.to_dict()
    if order["status"] != "pending":
        raise ValueError("Order already taken")

    transaction.update(
        order_ref,
        {
            "status": "assigned",
            "assignedDriverId": driver_id,
            "assignedAt": firestore.SERVER_TIMESTAMP,
        },
    )

    driver_ref = order_ref._client.collection("drivers").document(driver_id)
    transaction.update(driver_ref, {"currentOrderId": order_ref.id})


def handle_accept_failure(exception: Exception) -> dict:
    if isinstance(exception, ValueError):
        return {"success": False, "error": str(exception)}
    return {"success": False, "error": "Could not accept order"}


# Maps the status a driver is requesting -> the status the order must currently be in.
# Keeps status transitions server-authoritative, matching accept_order's pending-only guard.
REQUIRED_PRIOR_STATUS = {
    "picked_up": "assigned",
    "delivered": "picked_up",
}


@firestore.transactional
def update_order_status_transaction(
    transaction: Transaction, order_ref: DocumentReference, driver_id: str, new_status: str
) -> None:
    order_snapshot = order_ref.get(transaction=transaction)
    order = order_snapshot.to_dict()
    if order is None:
        raise ValueError("Order not found")
    if order.get("assignedDriverId") != driver_id:
        raise ValueError("Order is not assigned to this driver")

    required_prior_status = REQUIRED_PRIOR_STATUS[new_status]
    if order["status"] != required_prior_status:
        raise ValueError(f"Cannot mark order as {new_status} from status {order['status']}")

    transaction.update(order_ref, {"status": new_status})


def handle_update_status_failure(exception: Exception) -> dict:
    if isinstance(exception, ValueError):
        return {"success": False, "error": str(exception)}
    return {"success": False, "error": "Could not update order status"}
