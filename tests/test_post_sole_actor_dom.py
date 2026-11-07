# ruff: noqa: F811
"""The sole-actor binding: a member who manages no page, in any interface language.

Such a member has no account switcher, so the English picker labels never
apply. The comment box shows only that member's own avatar, and the binding
reads the avatar asset, never a label. Synthetic DOM; no LinkedIn network.
"""

import dataclasses

import pytest

from linkedin_mcp_server.linkedin.navigation import PageNavigator
from linkedin_mcp_server.linkedin.post_actions import (
    OWN_EDITOR_JS,
    PIN_EDITOR_JS,
    PIN_POST_ROOT_JS,
    SUBMIT_EDITOR_JS,
    PostActions,
)
from linkedin_mcp_server.linkedin.post_actors import PIN_SOLE_ACTOR_JS
from linkedin_mcp_server.linkedin.session import PageSession
from test_post_actions_dom import (
    ENGLISH,
    GERMAN,
    POST_ID,
    _clicked,
    _page_html,
    comment_editor,
    comment_thread,
    dom_page,  # noqa: F401 -- shared real Chromium fixture
    sdui_post,
)

pytestmark = [pytest.mark.browser_dom, pytest.mark.xdist_group("browser_runtime")]

FRENCH = dataclasses.replace(
    ENGLISH,
    locale="fr",
    react="Réagir à la publication de Varun",
    comment="Commenter la publication de Varun",
    repost="Republier la publication de Varun",
    send="Envoyer la publication de Varun dans un message",
    like="J’aime",
    submit="Commenter",
    reply="Répondre",
    emoji="Ouvrir le clavier Emoji",
    photo="Ajouter une photo",
)
ME = {"path": "/in/actor/", "name": "Example Member", "avatar": "PERSON"}


def photo(key: str) -> str:
    return (
        '<img width="32" height="32" src="https://media.licdn.com/dms/image/v2/'
        f'{key}/profile-displayphoto-shrink_100_100/x">'
    )


def member_post(labels, *, composer: str | None = None, comments: bool = True) -> str:
    """The detail page as a member with no page sees it: one avatar by the box."""
    editor = comment_editor(labels)
    html = sdui_post(labels)
    assert editor in html
    avatar = (
        f'<a href="/in/actor/">{photo("PERSON")}</a>' if composer is None else composer
    )
    html = html.replace(
        editor,
        f'<div class="composer">{avatar}<div class="box">{editor}</div></div>'
        + (comment_thread(labels) if comments else ""),
    )
    return html.replace(
        '<div class="actor">',
        f'<div class="actor"><a href="/in/author/">{photo("AUTHOR")}</a>',
        1,
    )


async def _root(page, html: str, lang: str):
    await page.set_content(_page_html(html))
    await page.evaluate("lang => { document.documentElement.lang = lang; }", lang)
    root = await page.evaluate_handle(PIN_POST_ROOT_JS, POST_ID)
    assert root.as_element() is not None
    return root


@pytest.mark.parametrize(
    "labels", [FRENCH, GERMAN, ENGLISH], ids=lambda labels: labels.locale
)
async def test_member_without_switcher_comments_in_any_language(dom_page, labels):
    root = await _root(dom_page, member_post(labels), labels.locale)
    session = PageSession(dom_page)
    actions = PostActions(session, PageNavigator(session))

    assert await actions._select_actor(root, ME) is root
    assert actions._actor_diagnostics is None

    pinned = await dom_page.evaluate_handle(PIN_EDITOR_JS, arg={"scope": root})
    editor = (await pinned.get_property("editor")).as_element()
    assert editor is not None
    await editor.click()
    await dom_page.keyboard.type("Belle lecture de silhouette")
    await dom_page.evaluate(
        OWN_EDITOR_JS, {"editor": editor, "text": "Belle lecture de silhouette"}
    )
    assert (
        await dom_page.evaluate(
            SUBMIT_EDITOR_JS, {"scope": root, "text": "Belle lecture de silhouette"}
        )
        == "submitted"
    )
    assert await _clicked(dom_page) == "comment-submit"


async def test_composer_avatar_swapped_after_binding_refuses_submit(dom_page):
    root = await _root(dom_page, member_post(FRENCH), "fr")
    session = PageSession(dom_page)
    assert await PostActions(session, PageNavigator(session))._select_actor(root, ME)

    pinned = await dom_page.evaluate_handle(PIN_EDITOR_JS, arg={"scope": root})
    editor = (await pinned.get_property("editor")).as_element()
    assert editor is not None
    await editor.click()
    await dom_page.keyboard.type("Bonjour")
    await dom_page.evaluate(OWN_EDITOR_JS, {"editor": editor, "text": "Bonjour"})
    await dom_page.evaluate(
        """() => { document.querySelector('.composer img').src =
          'https://media.licdn.com/dms/image/v2/OTHER/profile-displayphoto-shrink_100_100/x'; }"""
    )
    assert (
        await dom_page.evaluate(SUBMIT_EDITOR_JS, {"scope": root, "text": "Bonjour"})
        == "actor_changed"
    )
    assert await _clicked(dom_page) is None


@pytest.mark.parametrize(
    "composer,actor,expected",
    [
        (f'<a href="/in/other/">{photo("OTHER")}</a>', ME, "avatar_mismatch"),
        ("", ME, "no_avatar"),
        (
            f'<button aria-label="Changer de compte">{photo("PERSON")}</button>',
            ME,
            "unknown_switcher",
        ),
        (
            None,
            {"path": "/company/example/", "name": "Example", "avatar": "PERSON"},
            "unsupported_actor",
        ),
    ],
    ids=["other-member", "no-avatar", "unreadable-switcher", "company-actor"],
)
async def test_sole_binding_refuses_and_reports_why(
    dom_page, composer, actor, expected
):
    root = await _root(dom_page, member_post(FRENCH, composer=composer), "fr")
    session = PageSession(dom_page)
    actions = PostActions(session, PageNavigator(session))

    assert await actions._select_actor(root, actor) is None
    diagnostics = actions._actor_diagnostics
    assert diagnostics is not None
    assert diagnostics["actor_binding"] == expected
    assert diagnostics["lang"] == "fr"
    if expected == "unknown_switcher":
        assert diagnostics["label"] == "Changer de compte"
    refusal = actions._actor_unavailable("https://www.linkedin.com/x", "refused")
    assert refusal["status"] == "actor_unavailable"
    assert refusal["diagnostics"]["actor_binding"] == expected
    assert await _clicked(dom_page) is None


async def test_commenter_avatars_never_stand_in_for_the_composer(dom_page):
    # No avatar beside the box: the climb must stop at the comment list and the
    # action bar instead of reading a commenter's or the author's photo.
    root = await _root(dom_page, member_post(FRENCH, composer=""), "fr")
    assert (await dom_page.evaluate(PIN_SOLE_ACTOR_JS, {"root": root, "actor": ME}))[
        "status"
    ] == "no_avatar"
