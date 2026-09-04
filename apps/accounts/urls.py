from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("access/", views.UserAccessListView.as_view(), name="user_access_list"),
    path("access/grant/", views.GrantLocationAccessView.as_view(), name="grant_access"),
    path("access/<int:pk>/revoke/", views.RevokeLocationAccessView.as_view(), name="revoke_access"),
    path("users/create/", views.CreateUserView.as_view(), name="create_user"),
    path("users/<int:pk>/role/", views.SetUserRoleView.as_view(), name="set_user_role"),
    path(
        "users/<int:pk>/toggle-active/",
        views.ToggleUserActiveView.as_view(),
        name="toggle_user_active",
    ),
]
