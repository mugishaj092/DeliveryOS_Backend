# DeliveryOS Backend

Python Cloud Functions (2nd gen) backend for DeliveryOS — a delivery dispatch system. An order is created, the nearest online drivers are found and notified in real time, one driver accepts with no double-booking, and on delivery completion the driver's earnings are credited automatically.

This repo covers the **backend only**. The frontend (React Native driver app) is a separate codebase.

---

## 1. Setup Instructions

Condensed from `context/setup-guide.md` — enough to get the local emulator running. Assumes a Firebase project already exists (Firestore in Native mode, a service account key generated) and Node/Python are installed.

```bash
# 1. Install the Firebase CLI and log in
npm install -g firebase-tools
firebase login

# 2. Set up the Python environment
cd functions
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cd ..

# 3. Create your .env at the repo root (gitignored — never commit it)
cat > .env <<'EOF'
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/your-service-account.json
FIREBASE_PROJECT_ID=deliveryos-dev
EOF

# 4. Point local tools at the emulator (do this in every shell you use)
export FIRESTORE_EMULATOR_HOST="localhost:8080"

# 5. Start the emulator suite (Firestore, Functions, and the UI)
firebase emulators:start --only firestore,functions
```

Open `http://localhost:4000` for the Emulator UI. Firestore runs on `8080`, Functions on `5001`.

> **Note:** this project requires **JDK 21** for the emulator suite (`firebase-tools` resolves `java` off `PATH`, not `JAVA_HOME`). If your default JDK is older, prepend a JDK 21 `bin` directory to `PATH` in the shell you run `firebase emulators:start` from.

Once the emulators are up, seed test data (see §3) in a second shell with the same `FIRESTORE_EMULATOR_HOST` exported, then run the test suite:

```bash
export FIRESTORE_EMULATOR_HOST="localhost:8080"
export GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/your-service-account.json
cd functions && source venv/bin/activate && cd ..
pytest tests/ -v
```

`tests/test_accept_order_callable.py` calls `accept_order` over real HTTP and needs the `functions` emulator running too (already covered by the `--only firestore,functions` command above).

---

## 2. Environment Variables

| Variable | Used In | Notes |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | Local scripts, tests, emulator | Absolute path to a Firebase service account JSON key. Never committed — gitignored, keep it outside the repo. Still required even when talking only to the emulator, because `firebase_admin.initialize_app()` resolves a credential object via `google.auth.default()` before the emulator intercepts calls. |
| `FIREBASE_PROJECT_ID` | Firebase CLI, scripts | The Firebase project ID (`deliveryos-dev` for this build's `.firebaserc`). |
| `FIRESTORE_EMULATOR_HOST` | Scripts, functions, tests | Set to `localhost:8080` to target the local emulator instead of the live project. Unset (or absent) to write to production — `scripts/seed_test_data.py` prints which target it resolved at startup. |
| `RETRY_DELAY_SECONDS` | `functions/config.py` | Default `30`. Delay before the single dispatch retry fires, and reused as the "give up and expire" window after that retry runs. |
| `DRIVER_SEARCH_LIMIT` | `functions/config.py` | Default `3`. Max number of nearest online drivers notified per dispatch pass. |
| `ACCEPT_TIMER_SECONDS` | `functions/config.py` | Default `30`. Must match the frontend's accept-countdown UI. |

`RETRY_DELAY_SECONDS` and `DRIVER_SEARCH_LIMIT` are defined as constants in `functions/config.py` rather than read from the process environment at runtime — see that file for the actual values in effect. `ACCEPT_TIMER_SECONDS` has no backend-side constant of its own (there's nothing in `functions/` that enforces a 30-second accept window) — it's purely the frontend's countdown UI value, which must be kept in sync by hand with `RETRY_DELAY_SECONDS` since the backend's retry/give-up timing is what the countdown is visually representing.

---

## 3. Seeding Test Data

With the Firestore emulator running and `FIRESTORE_EMULATOR_HOST` exported:

```bash
python scripts/seed_test_data.py --reset
```

This creates 5 mock drivers (spread across `kigali-central`, `kigali-north`, and `kigali-east`, mixing online/offline) and 3 mock orders (at least one `pending`, so `on_order_created` fires immediately). `--reset` deletes any previously seeded `mock-driver-*`/`mock-order-*` documents first, so re-running never leaves duplicates. Pass `--fcm-token <token>` to assign a real device token to `mock-driver-1` for genuine push testing.

---

## 4. Architecture

### The three-function dispatch flow

Three Cloud Functions carry an order through its full lifecycle:

1. **`on_order_created`** (Firestore trigger, `orders/{orderId}` created) — looks up the nearest online drivers via a widening geohash-prefix query (never manual lat/long distance math), sends a data-only FCM push to each candidate, and schedules a single retry `RETRY_DELAY_SECONDS` later if nobody has accepted. An order with zero online drivers found is marked `expired` immediately instead of waiting.
2. **`accept_order`** (HTTPS callable) — a driver taps Accept in the app, which calls this. It runs a single Firestore transaction that reads the order, aborts if it's no longer `pending`, and otherwise atomically sets `orders.status = "assigned"` plus `drivers.currentOrderId`. This transaction is what makes the accept race condition safe: two simultaneous calls on the same order can never both succeed.
3. **`on_delivery_completed`** (Firestore trigger, `orders/{orderId}` updated, guarded to fire only on the transition *into* `delivered`) — looks up the order's zone-based fee and credits the assigned driver's `totalEarnings`/`pendingPayout` via `firestore.Increment` in the same `.update()` call that clears `currentOrderId`, so the driver becomes available again the instant earnings land.

A fourth trigger, `on_retry_due` (also on `orders/{orderId}` updated, guarded on `nextRetryAt` transitioning to a new non-null value), implements the retry itself: it fires the instant `on_order_created` schedules the retry, sleeps out the remaining delay inside the function body, then runs a second dispatch pass. If that second pass still finds nobody accepting after `RETRY_DELAY_SECONDS` more, the order is marked `expired`. This was chosen over Cloud Scheduler to get real ~30-second precision (Scheduler's minimum granularity is one minute) without provisioning any new infrastructure, keeping local development on the free Spark plan.

### Two schema fields added beyond the original spec

The original challenge spec's JSON examples don't include these two fields, but both are required to implement behavior the spec does ask for:

- **`orders.zone`** (string) — set at order creation, consumed by `earnings.get_zone_earnings(zone)` to look up the flat per-delivery fee for that order. Without it, zone-based earnings (an explicit requirement) has no input to key off of.
- **`drivers.geohash`** (string) — derived from `drivers.location` at the same precision used everywhere else in the codebase (`GEOHASH_PRECISION`), required for the nearest-driver lookup to run as a real indexed Firestore range query instead of hand-rolled distance math, which the project's invariants explicitly forbid.

### Order creation — resolved ambiguity

The challenge spec never defines who creates an order or through what interface — there's no customer-facing app in scope, and no `create_order` function among the three backend functions. This build resolves that by having `scripts/seed_test_data.py` create orders directly as Firestore documents, simulating what a customer-facing app would eventually do. `on_order_created` reacts purely to a `pending` order document appearing — it doesn't matter whether that document was written by the seed script, the Firestore console, or a future customer app. This keeps the backend's contract clean: anything that writes a valid `orders` document with `status: "pending"` triggers dispatch.

### Emulator-based, not live-deployed

**This submission was built and verified entirely against the Firebase Local Emulator Suite (Firestore + Functions), not a live Blaze deployment.** Every function, trigger, and the accept-race condition test were exercised with real trigger fires and real HTTP calls against the emulator — not mocks. Live deployment (`firebase deploy --only functions`) requires the Blaze plan and is a configuration step (see `context/setup-guide.md`, Step 10), not something built into this codebase; `firestore.rules` and `firestore.indexes.json` *have* been deployed live (`firebase deploy --only firestore:rules,firestore:indexes`), since that's free on Spark and was needed to confirm rules compile against the real project. The screen recording (§6) demonstrates the flow against the emulator.

---

## 5. Known Issues

- **Retry mechanism's implementation is a Firestore trigger that sleeps out the remaining delay in-process (`on_retry_due`), not a queue-based scheduler.** This works correctly and was live-verified, but it means a retry's ~30-second wait holds a live Cloud Functions invocation open the entire time (`timeout_sec=120` on that trigger to accommodate it). This is an acceptable simplification for a 3-day, Spark-plan build — see `context/progress-tracker.md`'s Decisions Made for the full reasoning — but a production system would more likely use Cloud Tasks or Cloud Scheduler so retries don't hold function instances open.
- **The nearest-driver geohash lookup is a known-imprecise proxy for true distance near geohash cell boundaries.** It's correct for the seeded test data (same-zone drivers always rank above other-zone drivers), but two points on opposite sides of a geohash cell edge can be geographically closer than two points that share more prefix characters. A production system would pair this with an actual geo-distance library rather than relying on shared-prefix length alone.
- **The "give up and expire" window after the single retry has no explicit duration in the original spec** (there's no third retry to catch a still-pending order otherwise). This build reuses `RETRY_DELAY_SECONDS` for that window rather than introducing a new constant — documented in `context/progress-tracker.md`, flagged here since it's an inferred value, not a specified one.
- No customer-facing order-creation interface exists, by design — see §4's Order Creation section. `scripts/seed_test_data.py` is the intended substitute for this submission.

---

## 6. Screen Recording

_Link to be added once recorded._ Will cover: dispatch → accept (including the race-condition handling) → complete → earnings, per the deliverables checklist below.

---

## Deliverables Checklist

- [x] GitHub repo with Cloud Functions code, `firestore.rules`, `requirements.txt`
- [x] `README.md` (this file)
- [x] `scripts/seed_test_data.py`
- [ ] Screen recording (5–7 min): dispatch → accept (race-condition handling) → complete → earnings
- [ ] `Euwamahoro` added as a GitHub collaborator — manual step via GitHub's web UI
- [ ] Submission email to `ubuntunowbusiness1@gmail.com`, subject `[Backend Challenge]`

---

## Repo Layout

```
functions/
├── main.py                   Trigger + callable entry points only
├── dispatch_logic.py         Nearest-driver lookup, retry scheduling/execution, expiry
├── transaction_handlers.py   accept_order transaction + failure handling
├── earnings.py                Zone -> fee mapping
├── notifications.py          FCM payload construction and send
├── schemas.py                 TypedDicts for Driver/Order
├── config.py                  Constants (retry delay, timer, search limit)
└── requirements.txt
firestore.rules
firestore.indexes.json
scripts/seed_test_data.py
tests/
context/                       Spec and planning docs used to build this
```
