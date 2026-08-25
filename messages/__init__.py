from . import admin_messages as _admin_messages
from . import user_messages as _user_messages

_admin_names = {name for name in dir(_admin_messages) if not name.startswith("_")}
_user_names = {name for name in dir(_user_messages) if not name.startswith("_")}

# Names deliberately defined in both modules; the user_messages variant wins in the
# merged namespace. Import `messages.admin_messages` directly for the admin variant.
_ALLOWED_SHADOWED_NAMES = {"something_went_wrong", "DEFAULT_LANG"}

_unexpected_collisions = (_admin_names & _user_names) - _ALLOWED_SHADOWED_NAMES
if _unexpected_collisions:
    raise RuntimeError(
        "Message name(s) defined in both admin_messages and user_messages would be "
        "silently shadowed: " + ", ".join(sorted(_unexpected_collisions))
    )

__all__ = sorted(_admin_names | _user_names)

globals().update({name: getattr(_admin_messages, name) for name in _admin_names})
globals().update({name: getattr(_user_messages, name) for name in _user_names})
