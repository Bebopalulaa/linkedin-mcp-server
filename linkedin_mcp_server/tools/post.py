"""
LinkedIn post/content search and post engagement tools.

Performs LinkedIn's global content search (the "Posts" results tab) using
innerText extraction, so informal "we're hiring" / "Buscamos ..." posts can
be found before a formal job listing is published. Mirrors search_people:
build a /search/results/content/ URL, scroll to load results, and return the
raw innerText for the LLM to parse, plus post-permalink references.
"""

import logging
from typing import Annotated, Any, Literal

import anyio
from fastmcp import Context, FastMCP
from pydantic import Field

from linkedin_mcp_server.config.schema import DEFAULT_TOOL_TIMEOUT_SECONDS
from linkedin_mcp_server.dependencies import get_ready_extractor, tool_errors
from linkedin_mcp_server.error_handler import CodedToolError, ErrorCode
from linkedin_mcp_server.linkedin.contracts import (
    POST_ACTION_INTERRUPTED_WARNING,
    FilterValidationError,
    before_the_reply_deadline,
    refuse_invalid_post_text,
)
from linkedin_mcp_server.linkedin.identifiers import (
    normalize_actor_reference,
    normalize_comment_reference,
    normalize_post_reference,
)

logger = logging.getLogger(__name__)

Reaction = Literal["like"]


async def _report_completion(
    ctx: Context, result: dict[str, Any], reply_deadline: float
) -> None:
    """Report completion without hiding an in-flight write.

    External cancellation can still interrupt the completed answer. The
    warning is quiet when the result says a retry is safe.
    """
    try:
        with before_the_reply_deadline(
            max(0.0, reply_deadline - anyio.current_time()) / 2
        ):
            await ctx.report_progress(progress=100, total=100, message="Complete")
    except BaseException:
        if result.get("retry_safe") is False:
            logger.warning(POST_ACTION_INTERRUPTED_WARNING)
        raise


def register_post_tools(
    mcp: FastMCP, *, tool_timeout: float = DEFAULT_TOOL_TIMEOUT_SECONDS
) -> None:
    """Register post/content-search tools with the MCP server."""

    @mcp.tool(
        timeout=tool_timeout,
        title="Search Posts",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"post", "search"},
    )
    @tool_errors
    async def search_posts(
        keywords: str,
        ctx: Context,
        date_posted: str | None = None,
        max_pages: Annotated[int, Field(ge=1, le=10)] = 3,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        """
        Search LinkedIn posts/content globally by keyword (the "Posts" tab).

        Use this to catch informal hiring posts ("we're hiring", "Buscamos
        ...", "estamos contratando", "join our team") that often appear before
        a formal job listing exists. This is global content search, distinct
        from get_feed (your own home feed) and get_company_posts (one
        company's page).

        Args:
            keywords: Search keywords (e.g., "Buscamos Unity", "AI automation hiring")
            ctx: FastMCP context for progress reporting
            date_posted: Optional recency filter. One of "past-24h",
                "past-week", "past-month"; the "past_24_hours" / "past_week" /
                "past_month" spellings used by search_jobs are accepted too.
                Omit for any time.
            content_type: Optional "Content type" filter. One of
                "documents" (PDFs and slide decks people upload), "images",
                "videos", "live_videos", "job_posts". Omit for all posts.
            max_pages: Scroll depth as result "pages" of ~5 scrolls each
                (1-10, default 3). Content search is an infinite scroll, so
                this caps how far the page is scrolled rather than fetching
                discrete pages.

        Returns:
            Dict with url, sections (search_results -> raw text), and optional
            references (post authors, companies, linked jobs, and kind
            "feed_post" permalinks read from the page's payload responses —
            /feed/update/<urn>/ or /posts/<slug>, both valid permalinks) and
            section_errors. The DOM carries no per-post permalink anchors;
            captured permalinks are not aligned to result order. The LLM
            should parse the raw text to extract each post's author,
            headline/role, company, body, posted date, and reaction/comment
            counts.
        """
        extractor = await get_ready_extractor(ctx, tool_name="search_posts")
        logger.info(
            "Searching posts: keywords='%s', date_posted='%s', content_type='%s', "
            "max_pages=%d",
            keywords,
            date_posted,
            content_type,
            max_pages,
        )

        await ctx.report_progress(progress=0, total=100, message="Starting post search")

        try:
            result = await extractor.search_posts(
                keywords,
                date_posted=date_posted,
                content_type=content_type,
                max_pages=max_pages,
            )
        except FilterValidationError as e:
            # Validation messages carry actionable detail; surface them as
            # ToolError so mask_error_details doesn't reduce them to a
            # generic "Error calling tool 'search_posts'".
            raise CodedToolError(str(e), ErrorCode.INVALID_ARGUMENT) from e

        await ctx.report_progress(progress=100, total=100, message="Complete")

        return result

    @mcp.tool(
        timeout=tool_timeout,
        title="React To Post",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"post", "actions"},
    )
    @tool_errors
    async def react_to_post(
        post: str,
        actor: str,
        ctx: Context,
        reaction: Reaction = "like",
        confirm_reaction: bool = False,
    ) -> dict[str, Any]:
        """
        React to one LinkedIn post, as the explicitly requested personal or company actor.

        This is a write operation and is publicly attributed: reactions are
        visible on the post and can appear in other members' feeds. Call first
        with confirm_reaction=False to inspect without clicking.

        Reacting is never a toggle here. If this account has already reacted,
        the tool returns ``already_reacted`` without clicking, because clicking a
        pressed reaction control on LinkedIn removes the reaction rather than
        changing it. To change or remove an existing reaction, do it in LinkedIn.

        Args:
            post: Permalink of one post, in either shape ``references`` returns
                for ``kind: "feed_post"`` — ``/feed/update/<urn>/`` or
                ``/posts/<slug>``. An absolute URL on any locale subdomain and a
                bare ``urn:li:{ugcPost,share,activity}:<id>`` are accepted too.
            ctx: FastMCP context for progress reporting
            actor: Exact personal /in/<member>/ or authorized /company/<company>/ URL.
            confirm_reaction: Must be True to add a reaction; defaults to False.
            reaction: Only "like" is supported; other reaction types are refused.

        Returns:
            Dict with url, status, message, acted, retry_safe and reaction.
            ``acted`` is true only after the reaction control reported itself as
            pressed; it does not claim anybody saw the reaction. ``retry_safe``
            is false from the moment a click is dispatched, and a retry while it
            is false can remove a reaction that did land.
        """
        reply_deadline = anyio.current_time() + tool_timeout
        post = normalize_post_reference(post)
        actor = normalize_actor_reference(actor)
        extractor = await get_ready_extractor(ctx, tool_name="react_to_post")
        logger.info("Reacting to post %s with %s", post, reaction)

        await ctx.report_progress(progress=0, total=100, message="Opening post")

        result = await extractor.react_to_post(
            post, actor=actor, reaction=reaction, confirm_reaction=confirm_reaction
        )
        await _report_completion(ctx, result, reply_deadline)
        return result

    @mcp.tool(
        timeout=tool_timeout,
        title="Comment On Post",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"post", "actions"},
    )
    @tool_errors
    async def comment_on_post(
        post: str,
        comment: str,
        actor: str,
        ctx: Context,
        confirm_comment: bool = False,
        mention_author: bool = False,
    ) -> dict[str, Any]:
        """
        Publish a comment on one LinkedIn post, as the explicitly requested personal or company actor.

        This is a write operation when confirm_comment is True, and it is public
        and attributed: the comment carries this account's name and can notify
        the post's author and other commenters. Call it first with
        confirm_comment=False to check that the post loads and offers a comment
        editor without typing anything.

        A member who manages no page has no account switcher. The comment box's
        own avatar then verifies the personal actor, in any interface language.
        An ``actor_unavailable`` refusal carries ``diagnostics`` naming what the
        page showed, and nothing is typed.

        Confirmation requires a new unique rendered comment identity with the
        exact body and requested actor's linked identity and avatar. A
        ``comment_unconfirmed`` status means the submit was
        dispatched and the text never appeared, which is not the same as a
        failure — open the post before retrying.

        Args:
            post: Permalink of one post, in either shape ``references`` returns
                for ``kind: "feed_post"``. See react_to_post for the accepted
                forms.
            mention_author: Prefix a verified rich mention of the post author.
                Refuses when the author or suggestion cannot be identified.
            comment: Text to publish. Newlines are allowed; every other control
                character is rejected before a browser is touched.
            actor: Exact personal /in/<member>/ or authorized /company/<company>/ URL.
            confirm_comment: Must be True to publish the comment
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url, status, message, acted and retry_safe. ``acted`` is
            true only when the submitted text was found rendered on the post.
            ``retry_safe`` is false from the moment the submit is dispatched;
            retrying while it is false can publish the comment twice.
        """
        reply_deadline = anyio.current_time() + tool_timeout
        actor = normalize_actor_reference(actor)
        # Answered before a session is acquired, for the reason send_message
        # gives: caller-owned text needs no browser, and acquiring one can spend
        # a login attempt and return an authentication error in place of the
        # refusal the caller can act on.
        refusal = refuse_invalid_post_text(
            normalize_post_reference(post), comment, field="comment"
        )
        if refusal is not None:
            return refusal

        extractor = await get_ready_extractor(ctx, tool_name="comment_on_post")
        logger.info("Commenting on post %s (confirm_comment=%s)", post, confirm_comment)

        await ctx.report_progress(progress=0, total=100, message="Opening post")

        result = await extractor.comment_on_post(
            post,
            comment,
            actor=actor,
            confirm_comment=confirm_comment,
            mention_author=mention_author,
        )
        await _report_completion(ctx, result, reply_deadline)
        return result

    @mcp.tool(
        timeout=tool_timeout,
        title="Get Post Comments",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"post", "comments"},
    )
    @tool_errors
    async def get_post_comments(
        post: str,
        ctx: Context,
        max_comments: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> dict[str, Any]:
        """Read currently rendered comment bodies and exact URN references on a post.

        This bounded read does not load all comments or hidden replies. The URL
        in each reference identifies the post, not a fabricated comment permalink.
        Pass the reference's value unchanged to reply_to_comment. Parent links
        are reported only where rendered ancestry proves them; otherwise the
        relationship is not_exposed. max_comments is capped at 50.
        """
        post = normalize_post_reference(post)
        extractor = await get_ready_extractor(ctx, tool_name="get_post_comments")
        return await extractor.get_post_comments(post, max_comments=max_comments)

    @mcp.tool(
        timeout=tool_timeout,
        title="Reply To Comment",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"post", "actions", "comments"},
    )
    @tool_errors
    async def reply_to_comment(
        post: str,
        comment_reference: str,
        reply: str,
        actor: str,
        ctx: Context,
        confirm_reply: bool = False,
        mention_parent_author: bool = False,
    ) -> dict[str, Any]:
        """Reply publicly to one exact rendered comment, as a verified member or company.

        Use get_post_comments and pass the exact reference value. Only currently
        rendered parents with an unambiguous Reply control and bounded composer
        are supported, including replies where that same structure is exposed.
        actor is an exact /in/<member>/ or authorized /company/<company>/ URL.
        confirm_reply=False opens and verifies the composer without typing or
        publishing; LinkedIn may insert its own automatic parent mention draft.
        With confirmation, the automatic mention is cleared by default. Set
        mention_parent_author=True to preserve its verified rich mention of the
        parent comment's author (not the post author). The reply text is otherwise
        exact. English reply labels are currently required.
        retry_safe=False means submission may have occurred: inspect the thread
        before retrying, including after an interrupted call.
        """
        reply_deadline = anyio.current_time() + tool_timeout
        post = normalize_post_reference(post)
        comment_reference = normalize_comment_reference(comment_reference)
        actor = normalize_actor_reference(actor)
        refusal = refuse_invalid_post_text(post, reply, field="reply")
        if refusal is not None:
            return refusal
        extractor = await get_ready_extractor(ctx, tool_name="reply_to_comment")
        result = await extractor.reply_to_comment(
            post,
            comment_reference,
            reply,
            actor=actor,
            confirm_reply=confirm_reply,
            mention_parent_author=mention_parent_author,
        )
        await _report_completion(ctx, result, reply_deadline)
        return result
