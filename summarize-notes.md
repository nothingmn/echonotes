Summarize the following transcript as factual Markdown.

## Core Rule

Use only information that is explicitly present in the transcript.
Do not infer, embellish, generalize, or fill in missing details.

If a detail is not stated in the transcript, do not invent it.

Never invent:
- participant names
- speaker roles
- decisions
- action items
- dates
- deadlines
- owners
- recommendations
- risks
- next steps

Never use placeholders such as:
- `[Owner]`
- `[Date]`
- `[Deadline]`
- `[Participants]`

## Output Requirements

Return Markdown with exactly these section headers in this order:

## Context
## Main Ideas
## Decisions
## Action Items
## Recommendations
## Insights
## Challenges and Risks
## Next Steps
## Minutes

Rules for each section:
- If the transcript does not support a section, write exactly `None stated.`
- Keep the wording concrete and close to the transcript.
- Do not quote large passages unless necessary.
- Do not repeat the same fact across multiple bullets unless required.

## Section Guidance

### Context
- Write 1 to 2 sentences maximum.
- Describe only what the transcript clearly is.

### Main Ideas
- List only actual themes or topics discussed.
- If there are no real themes, write `None stated.`

### Decisions
- Include only explicit decisions.
- If nothing was clearly decided, write `None stated.`

### Action Items
- Include only explicit commitments, assignments, or follow-ups.
- If none are stated, write `None stated.`

### Recommendations
- Include only advice or recommendations explicitly spoken in the transcript.
- If none are stated, write `None stated.`

### Insights
- Include only notable observations directly supported by the transcript.
- If the transcript is too short or trivial, write `None stated.`

### Challenges and Risks
- Include only problems, blockers, concerns, or risks actually mentioned.
- If none are stated, write `None stated.`

### Next Steps
- Include only explicit next steps.
- If none are stated, write `None stated.`

### Minutes
- Use a short bullet list of factual points in transcript order.
- One bullet per real point.
- Do not force a minimum count.
- For very short transcripts, 1 to 3 bullets is enough.

## Special Case For Very Short Or Test-Like Transcripts

If the transcript is extremely short, obviously a mic test, or contains no substantive meeting content:
- say that directly in `Context`
- set most sections to `None stated.`
- keep `Minutes` minimal and factual

## Final Rules

- Output only the Markdown summary.
- Do not add preambles or explanations.
- Do not mention these instructions.

# INPUT:
