from django.db import migrations, models

STATUSES = [
    ("in_stock", "In Stock"),
    ("reserved", "Reserved"),
    ("assigned", "Assigned"),
    ("in_use", "In Use"),
    ("delivered", "Delivered"),
    ("returned", "Returned"),
    ("damaged", "Damaged"),
    ("lost", "Lost"),
    ("disposed", "Disposed"),
]
MOVEMENTS = [
    ("receipt", "Receipt into stock"),
    ("transfer", "Location transfer"),
    ("reservation", "Reservation"),
    ("reservation_release", "Reservation release"),
    ("assignment", "Employee assignment"),
    ("put_in_use", "Put in internal use"),
    ("remove_from_use", "Return from internal use"),
    ("delivery", "Customer delivery"),
    ("return", "Return"),
    ("return_assessment", "Return assessment"),
    ("mark_damaged", "Mark damaged"),
    ("mark_lost", "Mark lost"),
    ("disposal", "Disposal"),
    ("correction", "Administrator correction"),
    ("reversal", "Reversal"),
    ("purpose_change", "Stock purpose reclassification"),
    ("install_component", "Install component"),
    ("remove_component", "Remove component"),
]


class Migration(migrations.Migration):
    dependencies = [("inventory", "0018_unitasset_name")]
    operations = [
        migrations.RemoveConstraint(
            model_name="inventorytransaction", name="txn_movement_type_valid"
        ),
        migrations.RemoveConstraint(model_name="unitasset", name="unitasset_status_valid"),
        migrations.AlterField(
            model_name="assetstatushistory",
            name="from_status",
            field=models.CharField(max_length=20, choices=STATUSES, null=True, blank=True),
        ),
        migrations.AlterField(
            model_name="assetstatushistory",
            name="to_status",
            field=models.CharField(max_length=20, choices=STATUSES),
        ),
        migrations.AlterField(
            model_name="inventorytransactionline",
            name="from_status",
            field=models.CharField(max_length=20, choices=STATUSES, null=True, blank=True),
        ),
        migrations.AlterField(
            model_name="inventorytransactionline",
            name="to_status",
            field=models.CharField(max_length=20, choices=STATUSES, null=True, blank=True),
        ),
        migrations.AlterField(
            model_name="unitasset",
            name="status",
            field=models.CharField(max_length=20, choices=STATUSES, default="in_stock"),
        ),
        migrations.AlterField(
            model_name="inventorytransaction",
            name="movement_type",
            field=models.CharField(max_length=25, choices=MOVEMENTS),
        ),
        migrations.AddConstraint(
            model_name="unitasset",
            constraint=models.CheckConstraint(
                condition=models.Q(status__in=[value for value, label in STATUSES]),
                name="unitasset_status_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventorytransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(movement_type__in=[value for value, label in MOVEMENTS]),
                name="txn_movement_type_valid",
            ),
        ),
    ]
