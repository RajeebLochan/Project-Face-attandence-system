"""Root URL configuration.

Biometric images are deliberately NOT served through ``MEDIA_URL``/``static()``:
they are only available from permission-checked views in ``app1``.
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("app1.urls")),
]

handler403 = "app1.views.errors.permission_denied"
handler404 = "app1.views.errors.page_not_found"
handler500 = "app1.views.errors.server_error"
