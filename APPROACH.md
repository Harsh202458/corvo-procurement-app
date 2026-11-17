# APPROACH

## Architecture
- **Engine** (`backend/app/engine`): deterministic, pure Python. Applies the active policy version (JSON) to PR lines.
- **Policy as data**: versioned JSON with effective dates; rules are ordered condition → outcome entries.
- **API**: FastAPI. **DB**: Postgres (SQLite locally/tests). **UI**: React + Vite + Tailwind.

## AI tools used
- **Building:** Claude Code (Anthropic) assisted with planning and writing code.
- **Inside the app:** Google Gemini (`gemini-3.8-flash`) for chat slot extraction, reading policy amendments into a
  JSON patch, and contract PDF extraction. The LLM **never decides routes**; every LLM output is validated and shown
  to a human before it takes effect. Rule-based fallbacks work when no LLM is configured.
