from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("access/", views.UserAccessListView.as_view(), name="user_access_list"),
    path("access/grant/", views.GrantLocationAccessView.as_view(), name="grant_access"),
    path("access/<int:pk>/revoke/", views.RevokeLocationAccessView.as_view(), name="revoke_access"),
]
