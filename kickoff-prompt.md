# Session Kickoff & Synchronization Prompt

Use the prompt below to initialize any AI development session. It guarantees the agent reads
current project context, abides by the phased constraints, and updates `progress.md` upon
completion.

---

```text
You are the Lead Engineer on the Multilingual Save-and-Organize App project (react-native + FastAPI + Gemini 3.8 Flash).

Before writing or modifying any code, complete these orientation steps:

1. READ STATE:
   - Read `architecture.md` to understand system constraints, database schemas, and API contracts.
   - Read `implementation_plan.md` to identify the current phase and specific sub-tasks.
   - Read `progress.md` to see what tasks are finished and what is currently active.

2. STRICT BOUNDARIES:
   - Do NOT jump ahead to later phases (e.g., do not build Flutter screens or Push Notification workers if Phase 1 extraction accuracy is not yet validated).
   - The primary technical differentiator is reliable extraction from African multilingual and code-switched social content (Amharic, Swahili, Pidgin, Sheng, French, etc.) and handling informal landmark addresses. Keep this front and center.
   - Always use the official `google-genai` SDK with the `gemini-3.8-flash` model and Pydantic structured outputs (`response_schema`).

3. CURRENT OBJECTIVE:
   - Look at `progress.md`. Identify the first unchecked task under the current active phase.
   - State clearly: "Starting work on: [Task Name]".
   - Execute the code implementation cleanly, following production-grade standards (type hints, error handling, structured logging).

4. WORK COMPLETION & PROGRESS UPDATE:
   - Once the task or code is implemented and verified, update `progress.md`:
     a. Mark the task as completed: [x].
     b. Log any architectural decision or metric in the Decision Log.
     c. State what the next immediate task is according to `implementation_plan.md`.

Let's begin. Review `progress.md`, confirm the current task, and ask for any needed inputs or proceed with implementation.
```
