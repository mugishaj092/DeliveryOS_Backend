# DeliveryOS Backend

A Python Cloud Functions (2nd gen) backend for **DeliveryOS**, a delivery dispatch system.

When a new order is created, the system:

* Finds the nearest online drivers.
* Sends them notifications in real time.
* Lets only one driver accept the order (prevents double booking).
* Credits the driver's earnings automatically after the order is delivered.

This repository contains **only the backend**. The React Native driver app is in a separate repository.

---

# 1. Setup

Before you begin:

* Create a Firebase project with Firestore (Native mode).
* Generate a Firebase service account key.
* Install Node.js and Python.

### Install Firebase CLI

```bash
npm install -g firebase-tools
firebase login
```

### Create a Python virtual environment

```bash
cd functions

python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

pip install -r requirements.txt

cd ..
```

### Create a `.env` file

Create a `.env` file in the project root.

```bash
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json
FIREBASE_PROJECT_ID=deliveryos-dev
```

> Do not commit this file to Git.

### Use the Firestore emulator

Run this in every terminal you use.

```bash
export FIRESTORE_EMULATOR_HOST=localhost:8080
```

### Start the emulators

```bash
firebase emulators:start --only firestore,functions
```

Open the Emulator UI:

```
http://localhost:4000
```

Ports:

* Firestore: **8080**
* Functions: **5001**

> This project requires **JDK 21**. If Java 21 is not your default version, make sure its `bin` folder is first in your `PATH`.

### Run the tests

After the emulator is running:

```bash
export FIRESTORE_EMULATOR_HOST=localhost:8080
export GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json

cd functions
source venv/bin/activate
cd ..

pytest tests/ -v
```

---

# 2. Environment Variables

| Variable                         | Purpose                                                                          |
| -------------------------------- | -------------------------------------------------------------------------------- |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to the Firebase service account key. Required even when using the emulator. |
| `FIREBASE_PROJECT_ID`            | Firebase project ID (for example `deliveryos-dev`).                              |
| `FIRESTORE_EMULATOR_HOST`        | Set to `localhost:8080` to use the local Firestore emulator.                     |
| `RETRY_DELAY_SECONDS`            | Time before retrying driver dispatch (default: 30 seconds).                      |
| `DRIVER_SEARCH_LIMIT`            | Maximum number of drivers notified in one dispatch (default: 3).                 |
| `ACCEPT_TIMER_SECONDS`           | Frontend countdown timer. Should match the backend retry delay.                  |

---

# 3. Seed Test Data

With the Firestore emulator running:

```bash
python scripts/seed_test_data.py --reset
```

This creates:

* 5 test drivers
* 3 test orders

Drivers are spread across different Kigali zones, and some are online while others are offline.

The `--reset` option removes old test data before creating new data.

To test real push notifications:

```bash
python scripts/seed_test_data.py --reset --fcm-token YOUR_TOKEN
```

---

# 4. System Architecture

The backend uses four Cloud Functions.

## 1. `on_order_created`

Triggered when a new order is added.

It:

* Finds the nearest online drivers using geohash search.
* Sends push notifications.
* Waits for a driver to accept.
* Schedules one retry after 30 seconds if nobody accepts.
* Marks the order as **expired** immediately if no online drivers are found.

---

## 2. `accept_order`

An HTTPS callable function.

When a driver presses **Accept**:

* Runs a Firestore transaction.
* Checks the order is still pending.
* Assigns the order to the driver.
* Updates the driver's current order.

Because everything happens inside one transaction, two drivers cannot accept the same order.

---

## 3. `update_order_status`

Another HTTPS callable function.

It allows the assigned driver to update the order:

* `assigned` → `picked_up`
* `picked_up` → `delivered`

It checks:

* The driver owns the order.
* The order is in the correct previous state.

---

## 4. `on_delivery_completed`

Triggered when an order becomes **delivered**.

It:

* Gets the delivery fee for the order's zone.
* Adds the earnings to the driver.
* Clears the driver's current order.
* Makes the driver available for new deliveries.

---

## Retry Flow

A fifth trigger, `on_retry_due`, handles retrying.

It:

1. Waits 30 seconds.
2. Tries to find drivers again.
3. Expires the order if nobody accepts after the retry.

This approach gives accurate 30-second timing without using Cloud Scheduler.

---

# Additional Database Fields

Two extra fields are used.

### `orders.zone`

Stores the delivery zone.

It is used to calculate how much the driver earns for that order.

### `drivers.geohash`

Stores the driver's location as a geohash.

This allows Firestore to find nearby drivers efficiently.

---

# Order Creation

The project does not include a customer app.

Instead, test orders are created by:

```bash
scripts/seed_test_data.py
```

Whenever a new order document with status `pending` is added, the backend automatically starts the dispatch process.

---

# Emulator Only

This project was developed and tested using the **Firebase Local Emulator Suite**.

The following were tested locally:

* Firestore triggers
* Cloud Functions
* Driver acceptance
* Race condition handling
* HTTP callable functions

Deploying to Firebase is a separate step and requires the Blaze plan.

---

# 5. Known Limitations

* Geohash search is only an approximation of true distance. Drivers near geohash boundaries may not always be ranked perfectly.
* The original specification does not define how long to wait after the retry before expiring an order. This project uses the same 30-second retry delay.
* There is no customer application. Test orders are created using the seed script instead.
