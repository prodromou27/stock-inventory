import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0003_stockreservation")]

    operations = [
        migrations.AddField(
            model_name="stockreservation",
            name="consumed_quantity",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="inventorytransactionline",
            name="reserved_quantity_delta",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="inventorytransactionline",
            name="stock_reservation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ledger_lines",
                to="inventory.stockreservation",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="inventorytransactionline",
            name="txnline_quantity_delta_valid",
        ),
        migrations.AddConstraint(
            model_name="inventorytransactionline",
            constraint=models.CheckConstraint(
                condition=(
                    (
                        models.Q(unit_asset__isnull=False)
                        & models.Q(quantity_delta__in=[-1, 1])
                        & models.Q(reserved_quantity_delta=0)
                    )
                    | (
                        models.Q(unit_asset__isnull=True)
                        & (~models.Q(quantity_delta=0) | ~models.Q(reserved_quantity_delta=0))
                    )
                ),
                name="txnline_quantity_delta_valid",
            ),
        ),
    ]
