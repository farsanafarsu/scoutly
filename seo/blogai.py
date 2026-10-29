"""AI writing for the Blog Bank (blogbank.py), through the Anthropic API. The key stays on the server.

  * ANTHROPIC_API_KEY turns it on; without it generation answers "not set up" and saved drafts still work
  * SCOUTLY_AI_MODEL picks the model (default claude-opus-5-5), SCOUTLY_AI_EFFORT the effort (default medium)

Each call streams (long blogs would otherwise risk HTTP timeouts) and asks for JSON that matches a schema, so
a draft either arrives complete or the call fails and the draft is marked for a retry. Refusals are re-run on
Anthropic's recommended fallback model server-side (fallbacks="default").
"""
import json
import os
import threading

from server import AuditError

MODEL = os.environ.get("SCOUTLY_AI_MODEL", "").strip() or "claude-opus-5-5"
EFFORT = os.environ.get("SCOUTLY_AI_EFFORT", "").strip() or "medium"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_client, _lock = None, threading.Lock()


class NotConfigured(AuditError):
    extra = {"notConfigured": True}


def configured():
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def _anthropic():
    global _client
    if not configured():
        raise NotConfigured("AI writing isn't set up on the server yet (ANTHROPIC_API_KEY). Saved blogs still open, edit and copy.")
    with _lock:
        if _client is None:
            try:
                import anthropic
            except ImportError:
                raise NotConfigured("The server is missing the anthropic package (pip install -r requirements.txt).")
            _client = anthropic.Anthropic(max_retries=3, timeout=600)
    return _client


def _call(system, prompt, schema, max_tokens):
    """One streamed request whose answer must match `schema`; returns the parsed JSON."""
    import anthropic
    client = _anthropic()
    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=max_tokens,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise AuditError("The server's Anthropic API key was rejected. Check ANTHROPIC_API_KEY on the server.")
    except anthropic.PermissionDeniedError:
        raise AuditError("The Anthropic API key isn't allowed to use this model. Check SCOUTLY_AI_MODEL or the key's workspace.")
    except anthropic.NotFoundError:
        raise AuditError(f"The AI model “{MODEL}” wasn't found. Check SCOUTLY_AI_MODEL on the server.")
    except anthropic.RateLimitError:
        raise AuditError("The AI service is rate-limiting requests right now. Retry this one in a minute.")
    except anthropic.BadRequestError as e:
        raise AuditError(f"The AI service rejected the request: {getattr(e, 'message', str(e))[:200]}")
    except anthropic.APIStatusError as e:
        raise AuditError(f"The AI service had a problem (HTTP {e.status_code}). Retry in a minute.")
    except anthropic.APIConnectionError:
        raise AuditError("Couldn't reach the AI service. Retry in a minute.")
    if msg.stop_reason == "refusal":
        raise AuditError("The AI declined to write this one. Rephrase the topic or keyword and retry.")
    if msg.stop_reason == "max_tokens":
        raise AuditError("The draft ran past the length limit before it finished. Lower the word count and retry.")
    text = next((b.text for b in msg.content if getattr(b, "type", "") == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        raise AuditError("The AI's answer wasn't in the expected format. Retry this one.")


# ------------------------------------------------------------------ a whole blog

BLOG_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "meta_title": {"type": "string"},
        "meta_description": {"type": "string"},
        "slug": {"type": "string"},
        "secondary_keywords": {"type": "array", "items": {"type": "string"}},
        "article_html": {"type": "string"},
        "faqs": {"type": "array", "items": {"type": "object", "properties": {"question": {"type": "string"}, "answer": {"type": "string"}},
                                             "required": ["question", "answer"], "additionalProperties": False}},
        "internal_links": {"type": "array", "items": {"type": "object", "properties": {"anchor_text": {"type": "string"}, "url": {"type": "string"}, "reason": {"type": "string"}},
                                                      "required": ["anchor_text", "url", "reason"], "additionalProperties": False}},
        "image_alts": {"type": "array", "items": {"type": "object", "properties": {"placement": {"type": "string"}, "alt_text": {"type": "string"}},
                                                  "required": ["placement", "alt_text"], "additionalProperties": False}},
    },
    "required": ["title", "meta_title", "meta_description", "slug", "secondary_keywords", "article_html", "faqs", "internal_links", "image_alts"],
    "additionalProperties": False,
}

SYSTEM = """You write SEO blog posts for small businesses and agencies. The drafts are pasted into the client's website, so they must read as finished, useful articles written for people first.

How to write:
- Match the requested language, location, audience, tone and length (within about 10% of the word count, not counting FAQs).
- Put the primary keyword in the title, naturally in the first 100 words, and in one or two H2s. Use secondary keywords and close variations where they fit; never stuff keywords.
- Structure: a short introduction (no heading), 4-8 H2 sections with H3s where they help, short paragraphs, lists or a table when they make something clearer, and a brief conclusion with a next step that suits the brand.
- Be specific and practical. Don't invent statistics, studies, quotes, prices, awards or client names. When a fact needs a source the writer must check, write it generally or wrap it as [check: ...] so the editor can verify it.
- Never promise rankings, guaranteed results or medical, legal or financial outcomes.

Output fields:
- title: the H1, about 50-65 characters.
- meta_title: 50-60 characters, keyword near the start. meta_description: 140-155 characters with a reason to click.
- slug: lowercase words joined by hyphens, 3-6 words, no dates or stop-word padding.
- secondary_keywords: 4-8 related phrases.
- article_html: the body only, without the H1 and without the FAQs. Allowed tags: h2, h3, h4, p, ul, ol, li, strong, em, a (href only), table, thead, tbody, tr, th, td, blockquote, br. No classes, styles, scripts, images or markdown.
- faqs: 4-6 questions people actually ask about the topic, each answered in 2-4 sentences.
- internal_links: 3-6 suggestions. If a list of the website's pages is given, use only URLs from that list, and also link 2-4 of them naturally inside article_html. If no list is given, set url to "" and describe the kind of page to link to in reason, and don't add internal links inside article_html.
- image_alts: 3-5 suggested images with where they go (e.g. "after the introduction") and descriptive alt text under 125 characters."""


def write_blog(brief, pages):
    """brief: topic, keyword, location, language, audience, words, tone, brand, website. pages: [(url, label)] from the site."""
    lines = [f"Topic: {brief['topic']}", f"Primary keyword: {brief['keyword']}"]
    for k, label in (("location", "Target location"), ("language", "Language"), ("audience", "Target audience"), ("tone", "Tone"), ("brand", "Brand / business details"), ("website", "Website")):
        if brief.get(k):
            lines.append(f"{label}: {brief[k]}")
    lines.append(f"Length: about {brief['words']} words")
    if brief.get("notes"):
        lines.append(f"Extra instructions: {brief['notes']}")
    if pages:
        lines.append("\nPages on the website you may link to (use only these URLs):")
        lines += [f"- {u} ({label})" for u, label in pages]
    out = _call(SYSTEM, "\n".join(lines), BLOG_SCHEMA, max_tokens=min(64000, 12000 + int(brief["words"]) * 6))
    if len((out.get("article_html") or "").strip()) < 200:
        raise AuditError("The AI returned an empty article. Retry this one.")
    return out


# ------------------------------------------------------------------ one section

SECTION_SCHEMA = {"type": "object", "properties": {"section_html": {"type": "string"}}, "required": ["section_html"], "additionalProperties": False}

SECTION_SYSTEM = """You rewrite one section of an SEO blog post. Return only that section as HTML: it starts with the same heading tag (you may improve the heading text) and runs until the next section would begin. Keep the language, tone and facts of the article; don't invent statistics, quotes or names, and don't promise rankings. Allowed tags: h2, h3, h4, p, ul, ol, li, strong, em, a (href only), table, thead, tbody, tr, th, td, blockquote, br."""


def rewrite_section(blog, section_html, instruction):
    prompt = "\n".join(filter(None, [
        f"Article title: {blog.get('title', '')}",
        f"Primary keyword: {blog.get('primaryKeyword', '')}",
        f"Language: {blog.get('brief', {}).get('language', '')}",
        f"Audience: {blog.get('brief', {}).get('audience', '')}",
        f"Tone: {blog.get('brief', {}).get('tone', '')}",
        f"What to change: {instruction or 'Improve it: clearer, more specific and more useful, about the same length.'}",
        "\nThe section to rewrite:\n" + section_html,
    ]))
    out = _call(SECTION_SYSTEM, prompt, SECTION_SCHEMA, max_tokens=16000)
    return out.get("section_html") or ""
