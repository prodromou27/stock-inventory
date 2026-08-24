"""The only module that writes InventoryTransaction/InventoryTransactionLine/
AssetStatusHistory rows or mutates StockBalance — every movement service
(receipts.py now; transfers/assignments/etc. from Phase 4) is built on these
primitives so there is exactly one code path for each kind of ledger write
(docs/architecture/01-repository-structure.md).
"""

from django.db import connection

from ..models import InventoryTransaction


def next_transaction_number():
    """Backed by a PostgreSQL SEQUENCE (apps/inventory/migrations/0002) —
    monotonic and unique but not gapless, which is fine: doc 02 only requires
    "unique sequential," and gapless numbering would force serializing every
    transaction write.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval('inventory_transaction_number_seq')")
        value = cursor.fetchone()[0]
    return f"TXN-{value:06d}"


def create_transaction_header(
    *,
    movement_type,
    performed_by,
    occurred_at,
    source_location=None,
    destination_location=None,
    project_reference="",
    final_customer="",
    employee_name="",
    is_temporary_assignment=None,
    expected_return_date=None,
    notes="",
    related_transaction=None,
    duplicate_serial_acknowledged=False,
):
    return InventoryTransaction.objects.create(
        transaction_number=next_transaction_number(),
        movement_type=movement_type,
        performed_by=performed_by,
        occurred_at=occurred_at,
        source_location=source_location,
        destination_location=destination_location,
        project_reference=project_reference,
        final_customer=final_customer,
        employee_name=employee_name,
        is_temporary_assignment=is_temporary_assignment,
        expected_return_date=expected_return_date,
        notes=notes,
        related_transaction=related_transaction,
        duplicate_serial_acknowledged=duplicate_serial_acknowledged,
    )
