from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import PasswordChangeView
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import ListView

from apps.core.authorization import ADMINISTRATOR, RoleRequiredMixin

from .forms import ROLE_CHOICES, CreateUserForm, GrantAccessForm, SetUserRoleForm
from .models import MustChangePassword, UserLocationAccess
from .services import (
    VALID_ROLES,
    create_user,
    grant_location_access,
    revoke_location_access,
    set_user_active,
    set_user_role,
)

User = get_user_model()


class UserAccessListView(LoginRequiredMixin, RoleRequiredMixin, ListView):
    allowed_roles = (ADMINISTRATOR,)
    template_name = "accounts/user_access_list.html"
    context_object_name = "users"

    def get_queryset(self):
        return User.objects.prefetch_related("groups", "location_access_grants__location").order_by(
            "username"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["role_choices"] = ROLE_CHOICES
        # Precomputed here (not in the template) so each row's <select> can
        # preselect the user's current role without a broken/expensive
        # per-row template lookup against a prefetched groups queryset.
        for u in context["users"]:
            role_names = {g.name for g in u.groups.all()}
            u.current_role = next((r for r in VALID_ROLES if r in role_names), None)
        return context


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


class CreateUserView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def get(self, request):
        return render(request, "accounts/create_user_form.html", {"form": CreateUserForm()})

    def post(self, request):
        form = CreateUserForm(request.POST)
        if not form.is_valid():
            return render(request, "accounts/create_user_form.html", {"form": form})

        data = form.cleaned_data
        try:
            new_user = create_user(
                created_by=request.user,
                username=data["username"],
                password=data["password"],
                role=data["role"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, "accounts/create_user_form.html", {"form": form})

        messages.success(
            request, f"User '{new_user.username}' created — they must change their password."
        )
        return redirect("accounts:user_access_list")


class SetUserRoleView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, pk):
        target_user = get_object_or_404(User, pk=pk)
        form = SetUserRoleForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Select a valid role.")
            return redirect("accounts:user_access_list")

        set_user_role(user=target_user, role=form.cleaned_data["role"], changed_by=request.user)
        messages.success(request, f"{target_user.username}'s role updated.")
        return redirect("accounts:user_access_list")


class ToggleUserActiveView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, pk):
        target_user = get_object_or_404(User, pk=pk)
        set_user_active(
            user=target_user, is_active=not target_user.is_active, changed_by=request.user
        )
        messages.success(
            request,
            f"{target_user.username} {'reactivated' if target_user.is_active else 'deactivated'}.",
        )
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
