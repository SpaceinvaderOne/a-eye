"""Prompt library — CRUD, rendering, validation, and export for customisable prompts."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default prompt templates (inserted on first run)
# ---------------------------------------------------------------------------

DEFAULT_VISION_PROMPT = """\
You are a photo file naming assistant. Look at this image and:
1. Describe the photo in one concise sentence.
2. Suggest a short, descriptive filename (4-8 words, no extension).
{{tags_step}}
{{quality_step}}

{{metadata}}
{{context}}
Rules for the filename:
- Use only lowercase letters, numbers, and hyphens
{{visible_rule}}
- Do NOT include camera model, date, resolution, or other metadata
- Do not include the file extension

{{format_instructions}}"""

DEFAULT_CONTEXT_TEMPLATE = """\
USER CONTEXT (from the photographer): {{user_context}}
IMPORTANT: You MUST incorporate this context into your description and filename. \
If the context names a subject, use that name (e.g. 'Shadow' not 'a dog'). \
If the context names an event or occasion, include it (e.g. 'Sams wedding' → 'sams-wedding-ceremony'). \
If the context names a location, include it (e.g. 'holiday in Crete' → 'crete-beach-sunset'). \
Only ignore context that is clearly about a completely different subject."""

# Stage constants
STAGE_VISION = "vision"
STAGE_CONTEXT = "context_injection"

# Required template variables per stage
_REQUIRED_VARS: dict[str, list[str]] = {
    STAGE_VISION: ["{{metadata}}"],
    STAGE_CONTEXT: ["{{user_context}}"],
}

# ---------------------------------------------------------------------------
# AI Helper prompts (copied to clipboard by "Create with AI" modal)
# ---------------------------------------------------------------------------

AI_HELPER_VISION = """\
I need help creating a custom vision prompt template for A-Eye, a photo management \
app that uses AI vision models (like minicpm-v or llava) to describe and rename photos.

## How It Works

A-Eye sends a text prompt along with a photo to a local AI vision model via Ollama. \
The model looks at the image, reads the prompt, and responds with a description, \
suggested filename, optional tags, and a quality assessment. The prompt template uses \
{{variables}} that A-Eye replaces with real values at processing time.

## Template Variables

Your template can use these variables (double curly braces). The system replaces \
them before sending to the model:

- {{metadata}} — REQUIRED. Replaced with formatted EXIF data (date, location, camera). \
Example: "Metadata:\\n- Date: 2024-03-15\\n- Location: London\\n- Camera: iPhone 15 Pro". \
If no metadata exists, shows "No metadata available".
- {{context}} — Replaced with a context injection block when the user provides processing \
context (e.g. "the dog is named Shadow"). Empty string when no context is provided. \
You don't need to write the context instructions — that's a separate template.
- {{visible_rule}} — Replaced with a filename rule that automatically changes based on \
whether context is present. Without context: "Describe only what is VISIBLE in the photo". \
With context: "Describe what is in the photo, incorporating any user-provided context".
- {{tags_step}} — Replaced with "3. List 5-15 descriptive keyword tags." when tag writing \
is enabled. Empty string when disabled.
- {{quality_step}} — Replaced with a quality assessment instruction. The step number is \
calculated automatically (3 or 4 depending on whether tags are enabled).
- {{format_instructions}} — Replaced with the required output format block. If you don't \
include this variable, it will be automatically appended to the end of your prompt. The \
format tells the model to respond with DESCRIPTION:, FILENAME:, TAGS: (if enabled), \
and QUALITY: lines.

## The Default Template (for reference)

You are a photo file naming assistant. Look at this image and:
1. Describe the photo in one concise sentence.
2. Suggest a short, descriptive filename (4-8 words, no extension).
{{tags_step}}
{{quality_step}}

{{metadata}}
{{context}}
Rules for the filename:
- Use only lowercase letters, numbers, and hyphens
{{visible_rule}}
- Do NOT include camera model, date, resolution, or other metadata
- Do not include the file extension

{{format_instructions}}

## Rules for the Template

1. You MUST include {{metadata}} somewhere in the template — validation will reject it otherwise.
2. You SHOULD include {{format_instructions}} — if you leave it out, it gets auto-appended, \
but it's better to place it where you want it.
3. You SHOULD include {{context}} if you want the context injection feature to work.
4. {{tags_step}} and {{quality_step}} should appear in your numbered instruction list so \
step numbers flow correctly.
5. The model's response must contain DESCRIPTION: and FILENAME: lines (the \
format_instructions variable handles this).
6. Filenames should use lowercase letters, numbers, and hyphens only (no spaces, no extension).

## Your Task

Please help me create a custom vision prompt template. Ask me questions ONE AT A TIME \
about my needs:

1. What type of photography do I primarily shoot? (wildlife, portraits, real estate, \
events, food, travel, product, scientific, general, etc.)
2. What's most important in my filenames? (subject detail, location, mood/style, \
technical aspects, etc.)
3. How descriptive should the AI's one-sentence description be? (brief and factual, \
or detailed and evocative?)
4. Any specific naming conventions or preferences? (e.g. always include colour, \
always mention the number of people, prefer specific terminology)
5. Anything the AI should specifically look for or ignore?

After gathering my answers, generate a complete template that:
- Includes all required {{variables}}
- Is tailored to my photography type
- Follows the rules above
- Works as a drop-in replacement for the default

Format your final output clearly and tell me to copy the template and paste it into \
A-Eye's Settings → Prompt Library → click "+ Add Custom Prompt", give it a name, \
and paste it in."""

AI_HELPER_CONTEXT = """\
I need help creating a custom context injection template for A-Eye, a photo management \
app that uses AI vision models to describe and rename photos.

## What Is a Context Injection Template?

When users process photos in A-Eye, they can optionally provide "processing context" — \
a short note like "holiday to Crete 2021" or "the dog is named Shadow" or "Sam's \
wedding reception". This context gets injected into the vision prompt to help the AI \
produce better descriptions and filenames.

The context injection template controls HOW that context is presented to the AI model. \
It's a small block of text that gets inserted into the main vision prompt whenever \
context is provided.

## Template Variable

Your template MUST include this variable:

- {{user_context}} — REQUIRED. Replaced with the user's actual context string (e.g. \
"the dog is named Shadow"). Maximum 500 characters.

This is the only variable available in the context injection template.

## The Default Template (for reference)

USER CONTEXT (from the photographer): {{user_context}}
IMPORTANT: You MUST incorporate this context into your description and filename. If the \
context names a subject, use that name (e.g. 'Shadow' not 'a dog'). If the context \
names an event or occasion, include it (e.g. 'Sams wedding' → 'sams-wedding-ceremony'). \
If the context names a location, include it (e.g. 'holiday in Crete' → \
'crete-beach-sunset'). Only ignore context that is clearly about a completely different \
subject.

## How the Context Template Is Used

The rendered context template gets placed inside the main vision prompt wherever \
{{context}} appears. If no processing context is provided by the user, the entire block \
is replaced with an empty string — so your template only activates when context exists.

## Tips for Good Context Templates

- Be forceful with the AI. Use words like "MUST", "ALWAYS", "IMPORTANT" — vision models \
tend to ignore soft suggestions.
- Give concrete examples of what TO do and what NOT to do. Anti-patterns ("say 'Shadow' \
not 'a dog'") work better than positive instructions alone.
- Cover different context types: subject names, events/occasions, locations, and time periods.
- Keep it concise — this gets injected into a larger prompt, so don't make it too long.

## Your Task

Please help me create a custom context injection template. Ask me questions ONE AT A TIME \
about my needs:

1. What kind of context do I typically provide? (pet names, event names, location info, \
people's names, project names, etc.)
2. How aggressively should the AI incorporate the context? (always override visual \
description, or blend naturally?)
3. Are there specific examples of context I commonly use that I can share?
4. Any terms or naming patterns the AI should follow when using my context?

After gathering my answers, generate a complete template that:
- Includes the required {{user_context}} variable
- Is tailored to my typical context usage
- Follows the tips above
- Works as a drop-in replacement for the default

Format your final output clearly and tell me to copy the template and paste it into \
A-Eye's Settings → Prompt Library → expand "Context Injection Template" → click \
"+ Add Custom Template", give it a name, and paste it in."""


# ---------------------------------------------------------------------------
# Startup — ensure default prompts exist
# ---------------------------------------------------------------------------

async def ensure_defaults(db: aiosqlite.Connection) -> None:
    """Insert default prompts if the prompts table is empty. Called on startup."""
    cursor = await db.execute("SELECT COUNT(*) FROM prompts")
    row = await cursor.fetchone()
    if row[0] > 0:
        return

    logger.info("Inserting default prompts into empty prompts table")

    await db.execute(
        "INSERT INTO prompts (name, stage, content, is_default, is_active) VALUES (?, ?, ?, 1, 1)",
        ("Default Vision Prompt", STAGE_VISION, DEFAULT_VISION_PROMPT),
    )
    await db.execute(
        "INSERT INTO prompts (name, stage, content, is_default, is_active) VALUES (?, ?, ?, 1, 1)",
        ("Default Context Injection", STAGE_CONTEXT, DEFAULT_CONTEXT_TEMPLATE),
    )
    await db.commit()
    logger.info("Default prompts created")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def get_active_prompt(db: aiosqlite.Connection, stage: str) -> dict | None:
    """Get the active prompt for a stage. Falls back to default if none active."""
    cursor = await db.execute(
        "SELECT * FROM prompts WHERE stage = ? AND is_active = 1 LIMIT 1", (stage,)
    )
    row = await cursor.fetchone()
    if row:
        return dict(row)

    # Fallback: get the default for this stage
    cursor = await db.execute(
        "SELECT * FROM prompts WHERE stage = ? AND is_default = 1 LIMIT 1", (stage,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def list_prompts(db: aiosqlite.Connection, stage: str | None = None) -> list[dict]:
    """List all prompts, optionally filtered by stage."""
    if stage:
        cursor = await db.execute(
            "SELECT * FROM prompts WHERE stage = ? ORDER BY is_default DESC, name ASC",
            (stage,),
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM prompts ORDER BY stage ASC, is_default DESC, name ASC"
        )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_prompt(db: aiosqlite.Connection, prompt_id: int) -> dict | None:
    """Get a single prompt by ID."""
    cursor = await db.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def create_prompt(
    db: aiosqlite.Connection, name: str, stage: str, content: str
) -> int:
    """Create a new custom prompt. Returns the new prompt ID."""
    if stage not in (STAGE_VISION, STAGE_CONTEXT):
        raise ValueError(f"Invalid stage: {stage}")

    errors = validate_prompt(stage, content)
    if errors:
        raise ValueError("; ".join(errors))

    cursor = await db.execute(
        "INSERT INTO prompts (name, stage, content, is_default, is_active) VALUES (?, ?, ?, 0, 0)",
        (name.strip(), stage, content),
    )
    await db.commit()
    logger.info("Created custom prompt %d: %s (%s)", cursor.lastrowid, name, stage)
    return cursor.lastrowid


async def update_prompt(
    db: aiosqlite.Connection, prompt_id: int, name: str | None = None, content: str | None = None
) -> None:
    """Update a custom prompt. Rejects updates to defaults."""
    prompt = await get_prompt(db, prompt_id)
    if not prompt:
        raise ValueError("Prompt not found")
    if prompt["is_default"]:
        raise ValueError("Cannot edit default prompts")

    updates: dict[str, Any] = {"updated_at": datetime.utcnow().isoformat()}
    if name is not None:
        updates["name"] = name.strip()
    if content is not None:
        errors = validate_prompt(prompt["stage"], content)
        if errors:
            raise ValueError("; ".join(errors))
        updates["content"] = content

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["_id"] = prompt_id
    await db.execute(f"UPDATE prompts SET {set_clause} WHERE id = :_id", updates)
    await db.commit()
    logger.info("Updated prompt %d", prompt_id)


async def delete_prompt(db: aiosqlite.Connection, prompt_id: int) -> None:
    """Delete a custom prompt. Rejects deletion of defaults or active prompt."""
    prompt = await get_prompt(db, prompt_id)
    if not prompt:
        raise ValueError("Prompt not found")
    if prompt["is_default"]:
        raise ValueError("Cannot delete default prompts")
    if prompt["is_active"]:
        raise ValueError("Cannot delete the active prompt — activate another one first")

    await db.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))
    await db.commit()
    logger.info("Deleted prompt %d", prompt_id)


async def set_active(db: aiosqlite.Connection, prompt_id: int) -> None:
    """Set a prompt as active for its stage. Deactivates the previous one."""
    prompt = await get_prompt(db, prompt_id)
    if not prompt:
        raise ValueError("Prompt not found")

    stage = prompt["stage"]

    # Deactivate all prompts for this stage
    await db.execute(
        "UPDATE prompts SET is_active = 0 WHERE stage = ?", (stage,)
    )
    # Activate the chosen one
    await db.execute(
        "UPDATE prompts SET is_active = 1 WHERE id = ?", (prompt_id,)
    )
    await db.commit()
    logger.info("Activated prompt %d for stage %s", prompt_id, stage)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_prompt(stage: str, content: str) -> list[str]:
    """Validate template has required variables. Returns list of errors (empty = valid)."""
    errors: list[str] = []
    required = _REQUIRED_VARS.get(stage, [])
    for var in required:
        if var not in content:
            errors.append(f"Missing required variable: {var}")
    return errors


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_vision_prompt(
    template: str,
    context_template: str,
    *,
    metadata_text: str,
    include_tags: bool,
    processing_context: str | None,
    output_language: str = "",
) -> str:
    """Render a vision prompt template with all dynamic variables injected.

    Args:
        template: The vision prompt template (with {{variables}}).
        context_template: The context injection template (with {{user_context}}).
        metadata_text: Pre-formatted metadata string from _format_metadata_for_prompt().
        include_tags: Whether to include the tags instruction step.
        processing_context: User-provided context string (or None).
        output_language: Language to request for DESCRIPTION/TAGS output (or "" for default).

    Returns:
        The fully rendered prompt string ready to send to the model.
    """
    # Build context section
    context_section = ""
    if processing_context and processing_context.strip():
        ctx = processing_context.strip()[:500]
        context_section = context_template.replace("{{user_context}}", ctx)
        context_section = "\n" + context_section + "\n"
        logger.info("Vision prompt includes context: %r", ctx)

    # Conditional visible rule
    if context_section:
        visible_rule = "- Describe what is in the photo, incorporating any user-provided context (names, events, locations)\n"
    else:
        visible_rule = "- Describe only what is VISIBLE in the photo (subject, action, setting)\n"

    # Tags step
    tags_step = "3. List 5-15 descriptive keyword tags.\n" if include_tags else ""

    # Quality step (number depends on tags)
    step_num = 4 if include_tags else 3
    quality_step = f"{step_num}. Assess image quality (flag issues or say ok).\n"

    # Format instructions
    line_count = 4 if include_tags else 3
    tags_line = "\nTAGS: <comma-separated keywords, 5-15 descriptive tags>" if include_tags else ""
    format_instructions = (
        f"Reply in EXACTLY this format ({line_count} lines, nothing else):\n"
        "DESCRIPTION: <your one-sentence description>\n"
        "FILENAME: <your-suggested-filename>"
        + tags_line
        + "\nQUALITY: ok (if no issues) OR comma-separated issues from: blurry, accidental, overexposed, underexposed, low-resolution"
    )

    # Replace template variables
    rendered = template
    rendered = rendered.replace("{{metadata}}", metadata_text)
    rendered = rendered.replace("{{context}}", context_section)
    rendered = rendered.replace("{{visible_rule}}", visible_rule)
    rendered = rendered.replace("{{tags_step}}", tags_step)
    rendered = rendered.replace("{{quality_step}}", quality_step)
    rendered = rendered.replace("{{format_instructions}}", format_instructions)

    # Safety net: auto-append format instructions if not present in output
    if "DESCRIPTION:" not in rendered or "FILENAME:" not in rendered:
        rendered = rendered.rstrip() + "\n\n" + format_instructions

    lang = output_language.strip()
    if lang and lang.lower() != "english":
        rendered += f"\nWrite the DESCRIPTION and TAGS in {lang}."

    return rendered


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_as_markdown(prompt: dict) -> str:
    """Format a prompt as shareable markdown with metadata header."""
    stage_label = "Vision Prompt" if prompt["stage"] == STAGE_VISION else "Context Injection Template"
    created = prompt.get("created_at", "")
    if created:
        # Trim to date only
        created = str(created)[:10]

    return (
        f"# {prompt['name']} — {stage_label}\n\n"
        f"**Stage:** {prompt['stage']}\n"
        f"**Created:** {created}\n"
        f"**For use with:** A-Eye\n\n"
        f"---\n\n"
        f"{prompt['content']}\n\n"
        f"---\n\n"
        f"*Exported from A-Eye. Paste into the \"Add Prompt\" form to import.*\n"
    )
