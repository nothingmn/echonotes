Extract structured Obsidian note data from the following transcript or summary.

Return JSON only. Do not return Markdown. Do not wrap the JSON in code fences.

Schema:
{
  "context": "string or null",
  "main_ideas": ["string"],
  "decisions": ["string"],
  "action_items": ["string"],
  "recommendations": ["string"],
  "insights": ["string"],
  "challenges_and_risks": ["string"],
  "next_steps": ["string"],
  "inferred_people": ["string"],
  "inferred_projects": ["string"],
  "inferred_topics": ["string"],
  "inferred_context": "string or null",
  "inferred_meeting_type": "string or null"
}

Rules:
- Use only information supported by the input.
- Never invent names, roles, deadlines, projects, or actions.
- `inferred_*` fields may contain careful interpretation, but only when strongly supported.
- Keep list items short and atomic.
- Use empty arrays when nothing is present.
- Use null for unknown single-value fields.
