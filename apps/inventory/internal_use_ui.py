from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View

from apps.core.authorization import ADMINISTRATOR, STOCK_MANAGER, RoleRequiredMixin
from apps.core.idempotency import (
    claim_submission_token,
    new_submission_token,
    release_submission_token,
)

from .forms import _apply_scoped_room_location
from .services.internal_use import change_internal_use, eligible_internal_use_assets


class InternalUseForm(forms.Form):
    occurred_at = forms.DateField(
        initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}), label="Date"
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), label="Installation / removal notes"
    )
    submission_token = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, user, returning=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit_asset_ids"] = forms.ModelMultipleChoiceField(
            queryset=eligible_internal_use_assets(user=user, returning=returning),
            label="Assets",
            widget=forms.MultipleHiddenInput,
        )
        self.order_fields(
            ["unit_asset_ids", "occurred_at", "notes", "location", "submission_token"]
        )
        if returning:
            self.fields["location"] = forms.ModelChoiceField(
                queryset=None, label="Receiving room / floor"
            )
            _apply_scoped_room_location(self.fields["location"], user)


class InternalUseView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR, STOCK_MANAGER)
    returning = False

    def get(self, request):
        form = InternalUseForm(
            user=request.user,
            returning=self.returning,
            initial={
                "unit_asset_ids": request.GET.getlist("unit_asset_ids"),
                "submission_token": new_submission_token(),
            },
        )
        return self.render_form(request, form)

    def post(self, request):
        form = InternalUseForm(request.POST, user=request.user, returning=self.returning)
        if form.is_valid():
            token = request.POST.get("submission_token")
            if not claim_submission_token(token):
                messages.info(request, "This action was already submitted.")
                return redirect("inventory:asset_list")
            try:
                txn = change_internal_use(
                    user=request.user,
                    unit_asset_ids=[asset.pk for asset in form.cleaned_data["unit_asset_ids"]],
                    occurred_at=form.cleaned_data["occurred_at"],
                    notes=form.cleaned_data["notes"],
                    returning=self.returning,
                    location=form.cleaned_data.get("location"),
                )
            except PermissionDenied:
                release_submission_token(token)
                raise
            except ValidationError as exc:
                release_submission_token(token)
                form.add_error(None, exc)
            else:
                messages.success(request, "Internal-use movement recorded.")
                return redirect(txn.get_absolute_url())
        return self.render_form(request, form)

    def render_form(self, request, form):
        return render(
            request,
            "inventory/internal_use_form.html",
            {
                "form": form,
                "returning": self.returning,
                "page_title": "Return from internal use" if self.returning else "Put in use",
                "assets": eligible_internal_use_assets(
                    user=request.user, returning=self.returning
                ).order_by("pk")[:200],
                "eligible_statuses": "in_use" if self.returning else "in_stock",
                "picker_internal_use": "remove" if self.returning else "install",
                "preselected_ids": (
                    request.POST.getlist("unit_asset_ids")
                    if request.method == "POST"
                    else request.GET.getlist("unit_asset_ids")
                ),
            },
        )
