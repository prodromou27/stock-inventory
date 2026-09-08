"""Concurrency regression tests — the first in this codebase (flagged during
this review as a gap: every oversell-prevention claim up to now was only
ever verified sequentially). `transaction=True` is required, not optional,
here: it disables the single enclosing atomic block pytest-django normally
wraps a test in, so each spawned thread gets its own real connection able to
see the setup data committed by the main thread, and the two threads'
select_for_update() calls genuinely contend against each other in Postgres
instead of running against one already-locked connection. `serialized_rollback=True`
is also required: a transactional test flushes every table afterward,
which would otherwise wipe the Administrator/StockManager/ReadOnlyUser
Group rows a data migration creates once for the whole run.
"""

import threading
from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.db import connection

from apps.inventory.models import InventoryTransaction, StockBalance, UnitAsset, UnitStatus
from apps.inventory.services.assignments import assign_to_employee, deliver_to_customer
from apps.inventory.services.internal_use import change_internal_use
from apps.inventory.services.receipts import receive_stock


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_internal_installation_racing_delivery_has_one_winner(
    administrator, unit_product, location_tree
):
    receive_stock(
        user=administrator,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="INSTALL-RACE",
    )
    asset = UnitAsset.objects.get(vendor_serial="INSTALL-RACE")

    def install():
        change_internal_use(
            user=administrator,
            unit_asset_ids=[asset.pk],
            occurred_at=date.today(),
            notes="Server room X",
        )

    def deliver():
        deliver_to_customer(
            user=administrator,
            final_customer="Customer",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

    assert sorted(_run_concurrently(install, deliver)) == ["rejected", "success"]
    assert asset.transaction_lines.exclude(transaction__movement_type="receipt").count() == 1


def _run_concurrently(*callables):
    """Runs each callable on its own thread with its own DB connection,
    waits for all of them, and returns "success"/"rejected" per callable in
    the same order they were given (never raises — a ValidationError from
    the callable is captured as "rejected" so the caller can assert on the
    outcome set instead of exception plumbing across threads).
    """
    results = [None] * len(callables)

    def _wrap(index, fn):
        try:
            fn()
            results[index] = "success"
        except ValidationError:
            results[index] = "rejected"
        finally:
            connection.close()

    threads = [threading.Thread(target=_wrap, args=(i, fn)) for i, fn in enumerate(callables)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_concurrent_assignments_cannot_oversell_the_same_quantity_balance(
    administrator, quantity_product, location_tree
):
    """Two threads race to assign away the last 5 on-hand units of the same
    StockBalance at once. adjust_balance()'s select_for_update() must
    serialize them — exactly one succeeds, the other is rejected for
    insufficient stock, and on_hand_quantity never goes negative.
    """
    receive_stock(
        user=administrator,
        product=quantity_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        quantity=5,
    )

    def _attempt():
        assign_to_employee(
            user=administrator,
            employee_name="Concurrent Tester",
            occurred_at=date.today(),
            quantity_lines=[
                {"product": quantity_product, "location": location_tree["room"], "quantity": 5}
            ],
        )

    results = _run_concurrently(_attempt, _attempt)

    assert sorted(results) == ["rejected", "success"]
    balance = StockBalance.objects.get(product=quantity_product, location=location_tree["room"])
    assert balance.on_hand_quantity == 0


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_concurrent_deliveries_of_the_same_unit_cannot_both_succeed(
    administrator, unit_product, location_tree
):
    """Two threads race to deliver the same single serialized unit at once.
    UnitAsset.select_for_update() in _issue_stock must serialize them —
    exactly one delivery is created for the asset, the other is rejected by
    validate_unit_transition() once it sees the asset is no longer In Stock.
    """
    receive_stock(
        user=administrator,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="SN-CONCURRENT-1",
    )
    asset = UnitAsset.objects.get(vendor_serial="SN-CONCURRENT-1")

    def _attempt():
        deliver_to_customer(
            user=administrator,
            final_customer="Race Co",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

    results = _run_concurrently(_attempt, _attempt)

    assert sorted(results) == ["rejected", "success"]
    asset.refresh_from_db()
    assert asset.status == UnitStatus.DELIVERED
    assert (
        InventoryTransaction.objects.filter(movement_type="delivery", lines__unit_asset=asset)
        .distinct()
        .count()
        == 1
    )
