"""
Access control shared by every page and API view.

Every view requires login. Reads need the view permission named by the view;
state-changing methods (POST, PUT, PATCH, DELETE) additionally need
`write_permission`. Anonymous users are redirected to the login page; signed-in
users without the permission get 403.
"""

from django.contrib.auth.mixins import PermissionRequiredMixin

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class RolePermissionMixin(PermissionRequiredMixin):
    permission_required = ()
    write_permission = None

    def get_permission_required(self):
        perms = super().get_permission_required() if self.permission_required else ()
        if self.request.method not in SAFE_METHODS:
            if not self.write_permission:
                raise ValueError(f"{type(self).__name__} accepts writes but has no write_permission")
            perms = (*perms, self.write_permission)
        return perms

    def has_permission(self):
        # Even a view with no specific permission requires login.
        return self.request.user.is_authenticated and super().has_permission()

    def can_write(self):
        return bool(self.write_permission) and self.request.user.has_perm(self.write_permission)
