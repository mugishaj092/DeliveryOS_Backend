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
