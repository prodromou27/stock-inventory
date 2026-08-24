from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from apps.core.authorization import ADMINISTRATOR, RoleRequiredMixin

from .forms import GrantAccessForm
from .models import UserLocationAccess
from .services import grant_location_access, revoke_location_access

User = get_user_model()


class UserAccessListView(LoginRequiredMixin, RoleRequiredMixin, ListView):
    allowed_roles = (ADMINISTRATOR,)
    template_name = "accounts/user_access_list.html"
    context_object_name = "users"

    def get_queryset(self):
        return User.objects.prefetch_related("groups", "location_access_grants__location").order_by(
            "username"
        )


class GrantLocationAccessView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def get(self, request):
        form = GrantAccessForm()
        return render(request, "accounts/grant_access_form.html", {"form": form})

    def post(self, request):
        form = GrantAccessForm(request.POST)
        if not form.is_valid():
            return render(request, "accounts/grant_access_form.html", {"form": form})

        grant_location_access(
            user=form.cleaned_data["user"],
            location=form.cleaned_data["location"],
            granted_by=request.user,
        )
        messages.success(request, "Access granted.")
        return redirect("accounts:user_access_list")


class RevokeLocationAccessView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, pk):
        access = get_object_or_404(UserLocationAccess, pk=pk)
        revoke_location_access(access=access, revoked_by=request.user)
        messages.success(request, "Access revoked.")
        return redirect("accounts:user_access_list")
