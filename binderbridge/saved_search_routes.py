"""Saved-search HTTP route handlers."""

from http import HTTPStatus


def saved_search_create(self, user):
    form = self.read_form()
    context = form.get("context", [""])[0]
    try:
        return_to = saved_search_safe_return_to(context, form.get("return_to", [""])[0])
        save_saved_search(user["id"], context, form.get("name", [""])[0], form)
    except ValueError as exc:
        content = f"""
        <section class="panel centered-state">
            <h1>Saved search not created</h1>
            <p class="muted">{e(exc)}</p>
            <a class="button primary" href="{e(saved_search_safe_return_to(context, form.get("return_to", [""])[0]) if context in SAVED_SEARCH_CONTEXTS else "/collection")}">Go back</a>
        </section>
        """
        return self.html(render_layout(user, "Saved search", content), HTTPStatus.BAD_REQUEST)
    return self.redirect(return_to)


def saved_search_delete(self, user, path):
    form = self.read_form()
    context = form.get("context", [""])[0]
    try:
        saved_search_id = int(path.strip("/").split("/")[1])
        return_to = saved_search_safe_return_to(context, form.get("return_to", [""])[0])
        delete_saved_search(user["id"], saved_search_id)
    except (ValueError, IndexError):
        return self.not_found(user)
    return self.redirect(return_to)


SAVED_SEARCH_ROUTE_METHODS = ("saved_search_create", "saved_search_delete")

__all__ = ["SAVED_SEARCH_ROUTE_METHODS", *SAVED_SEARCH_ROUTE_METHODS]
