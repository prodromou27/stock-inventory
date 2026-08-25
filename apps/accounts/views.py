from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import PasswordChangeView
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import ListView

from apps.core.authorization import ADMINISTRATOR, RoleRequiredMixin

from .forms import GrantAccessForm
from .models import MustChangePassword, UserLocationAccess
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


class ForcedPasswordChangeView(PasswordChangeView):
    """Shadows django.contrib.auth.urls' "password_change" URL name (wired
    ahead of that include() in config/urls.py) so a successful change also
    clears MustChangePassword — see apps.accounts.middleware and doc 04's
    "Default admin bootstrap" section. Used by every user for a voluntary
    password change too, not just the forced first one; clearing a row that
    was never there is a harmless no-op.
    """

    template_name = "registration/password_change_form.html"
    success_url = reverse_lazy("core:home")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["must_change_password"] = MustChangePassword.objects.filter(
            user=self.request.user
        ).exists()
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        MustChangePassword.objects.filter(user=self.request.user).delete()
        messages.success(self.request, "Password changed.")
        return response
